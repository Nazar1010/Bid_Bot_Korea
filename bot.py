"""J-Bid Korea — Telegram-бот: отчёт и стоимость под ключ по объявлению Encar.

Запуск:  python bot.py
"""
import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputMediaPhoto, Message)

import config
import texts
from calc import turnkey
from calc.rates import get_rates
from encar.client import CarNotFound, EncarClient, EncarError, extract_car_id, photo_url
from encar.parser import CarReport, build_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("korea_bot")

router = Router()
encar = EncarClient()

_reports: dict[str, tuple[float, CarReport]] = {}   # car_id → (время, отчёт)
_hp: dict[tuple[int, str], int] = {}                 # (user, car_id) → л.с.
_awaiting_hp: dict[int, str] = {}                    # user → car_id, ждём ввод мощности
_busy: set[int] = set()
_stats = {"requests": 0, "ok": 0, "errors": 0, "started": datetime.now()}


# ── Данные ──────────────────────────────────────────────────────────────────
async def get_report(car_id: str) -> CarReport:
    cached = _reports.get(car_id)
    if cached and time.time() - cached[0] < config.REPORT_CACHE_MINUTES * 60:
        return cached[1]
    raw = await encar.fetch_all(car_id)
    rep = build_report(raw)
    _reports[car_id] = (time.time(), rep)
    if len(_reports) > 500:                         # не раздуваем память
        for k, _ in sorted(_reports.items(), key=lambda kv: kv[1][0])[:100]:
            _reports.pop(k, None)
    return rep


async def get_turnkey(rep: CarReport, user_id: int):
    rates = await get_rates()
    return turnkey.calculate(rep, rates, hp=_hp.get((user_id, rep.car_id)))


def keyboard(car_id: str, n_photos: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📋 Страховая история", callback_data=f"h:{car_id}"),
         InlineKeyboardButton(text="🔧 Техосмотр", callback_data=f"i:{car_id}")],
        [InlineKeyboardButton(text="💰 Расчёт подробно", callback_data=f"c:{car_id}"),
         InlineKeyboardButton(text="⚡ Указать мощность", callback_data=f"hp:{car_id}")],
    ]
    if n_photos > config.MAX_PHOTOS_IN_ALBUM:
        rows.append([InlineKeyboardButton(text=f"📷 Ещё фото ({n_photos - config.MAX_PHOTOS_IN_ALBUM})",
                                          callback_data=f"ph:{car_id}")])
    rows.append([InlineKeyboardButton(text="✉️ Хочу это авто", callback_data=f"lead:{car_id}"),
                 InlineKeyboardButton(text="🔗 Encar", url=f"https://fem.encar.com/cars/detail/{car_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_photos(message: Message, rep: CarReport, start: int = 0):
    paths = rep.photos[start:start + config.MAX_PHOTOS_IN_ALBUM]
    if not paths:
        return
    sem = asyncio.Semaphore(5)

    async def load(p):
        async with sem:
            return await encar.download_photo(p)

    blobs = await asyncio.gather(*(load(p) for p in paths))
    media = []
    for i, (p, b) in enumerate(zip(paths, blobs)):
        src = BufferedInputFile(b, filename=f"{rep.car_id}_{start + i}.jpg") if b else photo_url(p)
        media.append(InputMediaPhoto(media=src))
    try:
        if len(media) == 1:
            await message.answer_photo(media[0].media)
        else:
            await message.answer_media_group(media)
    except Exception as e:  # noqa: BLE001  — фото не критичны, отчёт всё равно уйдёт
        log.warning("photos %s: %s", rep.car_id, e)


# ── Команды ─────────────────────────────────────────────────────────────────
@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message):
    await message.answer(texts.welcome(), disable_web_page_preview=True)


@router.message(Command("rates"))
async def cmd_rates(message: Message):
    r = await get_rates()
    await message.answer(
        f"💱 Курс ЦБ на {texts.e(r['date'])}:\n1000 ₩ = {r['KRW'] * 1000:.2f} ₽\n"
        f"1 € = {r['EUR']:.2f} ₽\n1 $ = {r['USD']:.2f} ₽\n<i>Источник: {texts.e(r['source'])}</i>")


@router.message(Command("check"))
async def cmd_check(message: Message):
    if config.ADMIN_IDS and message.from_user.id not in config.ADMIN_IDS:
        return
    await message.answer("⏳ Проверяю доступ к Encar с этого сервера…")
    res = await encar.ping()
    proxy = "через прокси" if config.ENCAR_PROXY else "напрямую (без прокси)"
    await message.answer(f"<b>Encar ({proxy}):</b>\n<code>{texts.e(res)}</code>\n\n"
                         "Если «ОШИБКА» или HTTP не 200 — впишите ENCAR_PROXY в config.py.")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    s = _stats
    await message.answer(f"С {s['started']:%d.%m %H:%M}: запросов {s['requests']}, "
                         f"успешно {s['ok']}, ошибок {s['errors']}, в кэше {len(_reports)}")


# ── Мощность ────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("hp:"))
async def cb_hp(call: CallbackQuery):
    car_id = call.data.split(":", 1)[1]
    _awaiting_hp[call.from_user.id] = car_id
    await call.message.answer("⚡ Напишите мощность двигателя в л.с. (например, <code>180</code>).\n"
                              "Для гибрида — суммарную (ДВС + электромотор).")
    await call.answer()


@router.message(F.text.regexp(r"^\s*\d{2,4}\s*(л\.?\s*с\.?|hp|лс)?\s*$"))
async def msg_hp(message: Message):
    car_id = _awaiting_hp.get(message.from_user.id)
    if not car_id:
        return await msg_any(message)
    hp = int("".join(ch for ch in message.text if ch.isdigit()))
    if not 40 <= hp <= 1500:
        return await message.answer("Похоже на опечатку — введите мощность от 40 до 1500 л.с.")
    _awaiting_hp.pop(message.from_user.id, None)
    _hp[(message.from_user.id, car_id)] = hp
    try:
        rep = await get_report(car_id)
    except EncarError:
        return await message.answer("Не удалось обновить данные Encar, попробуйте ещё раз.")
    tk = await get_turnkey(rep, message.from_user.id)
    await message.answer(texts.cost(tk, rep), reply_markup=keyboard(car_id, len(rep.photos)))


# ── Основной сценарий: ссылка → отчёт ───────────────────────────────────────
@router.message(F.text)
async def msg_any(message: Message):
    car_id = extract_car_id(message.text or "")
    if not car_id:
        if "kbchachacha" in (message.text or "") or "kcar" in (message.text or ""):
            return await message.answer("Пока работаю только с Encar. Пришлите ссылку с encar.com.")
        return await message.answer("Пришлите ссылку на объявление Encar или его номер.\n"
                                    "Пример: <code>https://fem.encar.com/cars/detail/41000001</code>")
    uid = message.from_user.id
    if uid in _busy:
        return await message.answer("⏳ Ещё обрабатываю предыдущий запрос…")
    _busy.add(uid)
    _awaiting_hp.pop(uid, None)
    _stats["requests"] += 1
    wait = await message.answer(f"⏳ Загружаю объявление №{car_id} с Encar…")
    try:
        rep = await get_report(car_id)
        tk = await get_turnkey(rep, uid)
        await send_photos(message, rep)
        await message.answer(texts.card(rep, tk), reply_markup=keyboard(car_id, len(rep.photos)),
                             disable_web_page_preview=True)
        _stats["ok"] += 1
    except CarNotFound:
        _stats["errors"] += 1
        await message.answer(f"❌ Объявление №{car_id} не найдено — возможно, авто уже продано.")
    except EncarError as e:
        _stats["errors"] += 1
        log.error("encar %s: %s", car_id, e)
        await message.answer("⚠️ Encar сейчас не отвечает. Попробуйте через пару минут "
                             f"или напишите менеджеру @{config.MANAGER_USERNAME}.")
    except Exception:  # noqa: BLE001
        _stats["errors"] += 1
        log.exception("report %s", car_id)
        await message.answer("⚠️ Не получилось разобрать объявление. Менеджер посмотрит вручную: "
                             f"@{config.MANAGER_USERNAME}")
    finally:
        _busy.discard(uid)
        try:
            await wait.delete()
        except Exception:  # noqa: BLE001
            pass


async def _cached_or_fetch(call: CallbackQuery) -> CarReport | None:
    car_id = call.data.split(":", 1)[1]
    try:
        return await get_report(car_id)
    except EncarError:
        await call.answer("Encar не отвечает, попробуйте позже", show_alert=True)
        return None


@router.callback_query(F.data.startswith("h:"))
async def cb_history(call: CallbackQuery):
    rep = await _cached_or_fetch(call)
    if rep:
        r = await get_rates()
        await call.message.answer(texts.history(rep, r["KRW"]))
        await call.answer()


@router.callback_query(F.data.startswith("i:"))
async def cb_inspection(call: CallbackQuery):
    rep = await _cached_or_fetch(call)
    if rep:
        await call.message.answer(texts.inspection(rep))
        await call.answer()


@router.callback_query(F.data.startswith("c:"))
async def cb_cost(call: CallbackQuery):
    rep = await _cached_or_fetch(call)
    if rep:
        tk = await get_turnkey(rep, call.from_user.id)
        await call.message.answer(texts.cost(tk, rep))
        await call.answer()


@router.callback_query(F.data.startswith("ph:"))
async def cb_photos(call: CallbackQuery):
    rep = await _cached_or_fetch(call)
    if rep:
        await call.answer("Загружаю фото…")
        for start in range(config.MAX_PHOTOS_IN_ALBUM, min(len(rep.photos), 40), config.MAX_PHOTOS_IN_ALBUM):
            await send_photos(call.message, rep, start)


@router.callback_query(F.data.startswith("lead:"))
async def cb_lead(call: CallbackQuery, bot: Bot):
    rep = await _cached_or_fetch(call)
    if not rep:
        return
    u = call.from_user
    who = f"@{u.username}" if u.username else f"id {u.id}"
    tk = await get_turnkey(rep, u.id)
    total = f"{texts.rub(tk.total_rub)}" if tk.ok else "ручной расчёт"
    note = (f"🔔 <b>Заявка Корея</b>\n{texts.e(rep.title)}, {texts.e(rep.year_month)}, "
            f"{texts.num(rep.mileage)} км\nЦена: {texts.krw(rep.price_krw)} · под ключ ≈ {total}\n"
            f"{rep.url}\nКлиент: {texts.e(u.full_name)} ({who})")
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, note, disable_web_page_preview=True)
        except Exception as e:  # noqa: BLE001
            log.warning("lead → %s: %s", admin, e)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="Написать менеджеру", url=f"https://t.me/{config.MANAGER_USERNAME}")]])
    await call.message.answer("✅ Заявка передана менеджеру. Он свяжется с вами, или напишите сами:",
                              reply_markup=kb)
    await call.answer()


async def main():
    if not config.BOT_TOKEN or "ВСТАВЬТЕ" in config.BOT_TOKEN:
        raise SystemExit("Впишите BOT_TOKEN в config.py")
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    log.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await encar.close()


if __name__ == "__main__":
    asyncio.run(main())
