"""J-Bid Korea — Telegram-бот: отчёт и стоимость под ключ по объявлению Encar.

Ручной режим: бот САМ НА ENCAR НЕ ХОДИТ. Он даёт ссылку, вы открываете её
в своём браузере и пересылаете ответ (файлом или текстом) — бот разбирает.

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
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto, Message)

import config
import texts
from calc import turnkey
from calc.rates import get_rates
from encar import manual
from encar.client import extract_car_id, photo_url
from encar.parser import CarReport, build_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("korea_bot")

router = Router()

_sessions: dict[int, dict] = {}                  # user → {car_id, vehicle, record, inspection, diagnosis}
_reports: dict[str, CarReport] = {}              # car_id → последний отчёт
_hp: dict[tuple[int, str], int] = {}             # (user, car_id) → л.с.
_awaiting_hp: dict[int, str] = {}                # user → car_id, ждём ввод мощности
_buffers: dict[int, tuple[float, str]] = {}      # user → (время, накопленный текст JSON)
_stats = {"reports": 0, "started": datetime.now()}

SECTION_NAMES = {"vehicle": "данные авто", "record": "страховую историю",
                 "inspection": "техосмотр", "diagnosis": "диагностику Encar"}
SECTION_ICONS = {"vehicle": "🚗", "record": "📋", "inspection": "🔧", "diagnosis": "🩺"}


# ── Помощники ───────────────────────────────────────────────────────────────
async def get_turnkey(rep: CarReport, user_id: int):
    return turnkey.calculate(rep, await get_rates(), hp=_hp.get((user_id, rep.car_id)))


def rebuild(uid: int) -> CarReport:
    s = _sessions[uid]
    rep = build_report({"car_id": s["car_id"], "vehicle": s["vehicle"], "record": s.get("record"),
                        "record_hidden": s.get("record_hidden", "record" not in s.get("links", {})),
                        "inspection": s.get("inspection"), "diagnosis": s.get("diagnosis")})
    _reports[rep.car_id] = rep
    if len(_reports) > 300:
        _reports.pop(next(iter(_reports)))
    return rep


def open_button(kind: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=f"{SECTION_ICONS[kind]} Открыть {SECTION_NAMES[kind]}", url=url)


def keyboard(uid: int, rep: CarReport) -> InlineKeyboardMarkup:
    s = _sessions.get(uid, {})
    links = s.get("links", {})
    rows = []
    missing = [k for k in ("record", "inspection", "diagnosis") if k in links and not s.get(k)]
    for k in missing:                                   # разделы, которые ещё не прислали
        rows.append([open_button(k, links[k])])
    rows += [
        [InlineKeyboardButton(text="📋 Страховая история", callback_data=f"h:{rep.car_id}"),
         InlineKeyboardButton(text="🔧 Техосмотр", callback_data=f"i:{rep.car_id}")],
        [InlineKeyboardButton(text="💰 Расчёт подробно", callback_data=f"c:{rep.car_id}"),
         InlineKeyboardButton(text="⚡ Указать мощность", callback_data=f"hp:{rep.car_id}")],
    ]
    if len(rep.photos) > config.MAX_PHOTOS_IN_ALBUM:
        rows.append([InlineKeyboardButton(text=f"📷 Ещё фото ({len(rep.photos) - config.MAX_PHOTOS_IN_ALBUM})",
                                          callback_data=f"ph:{rep.car_id}")])
    rows.append([InlineKeyboardButton(text="✉️ Хочу это авто", callback_data=f"lead:{rep.car_id}"),
                 InlineKeyboardButton(text="🔗 Encar", url=rep.url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_photos(message: Message, rep: CarReport, start: int = 0):
    """Фото отправляются ссылками — их забирает Telegram, сервер бота на Encar не ходит."""
    paths = rep.photos[start:start + config.MAX_PHOTOS_IN_ALBUM]
    if not paths:
        return
    try:
        if len(paths) == 1:
            await message.answer_photo(photo_url(paths[0]))
        else:
            await message.answer_media_group([InputMediaPhoto(media=photo_url(p)) for p in paths])
    except Exception as e:  # noqa: BLE001  — фото не критичны
        log.warning("photos %s: %s", rep.car_id, e)
        await message.answer("📷 Фото не загрузились — смотрите на Encar.")


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


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await message.answer(f"С {_stats['started']:%d.%m %H:%M}: отчётов {_stats['reports']}, "
                         f"в памяти {len(_reports)}")


# ── Приём данных Encar (файл или текст) ─────────────────────────────────────
async def handle_payload(message: Message, data) -> None:
    uid = message.from_user.id
    kind = manual.detect_kind(data)
    if not kind:
        await message.answer("Не узнаю эти данные. Пришлите ответ одной из страниц, "
                             "которые даёт бот (данные авто, история, техосмотр).")
        return
    s = _sessions.get(uid)

    if kind == "bundle":
        car_id = str(data.get("car_id") or data["vehicle"].get("vehicleId") or "")
        _sessions[uid] = s = {"car_id": car_id, "vehicle": data["vehicle"], "links": {},
                              "record": data.get("record"), "inspection": data.get("inspection"),
                              "diagnosis": data.get("diagnosis"),
                              "record_hidden": bool(data.get("record_hidden"))
                              or ((data["vehicle"].get("condition") or {}).get("accident") or {})
                              .get("recordView") is False}
        _awaiting_hp.pop(uid, None)
        rep = rebuild(uid)
        _stats["reports"] += 1
        tk = await get_turnkey(rep, uid)
        await send_photos(message, rep)
        await message.answer(texts.card(rep, tk), reply_markup=keyboard(uid, rep),
                             disable_web_page_preview=True)
        r = await get_rates()
        await message.answer(texts.history(rep, r["KRW"]))
        await message.answer(texts.inspection(rep))
        return

    if kind == "vehicle":
        car_id = str(data.get("vehicleId") or "")
        if s and s.get("car_id") and not s.get("vehicle"):
            car_id = s["car_id"]                         # номер из ссылки, которую прислали раньше
        _sessions[uid] = s = {"car_id": car_id, "vehicle": data, "links": manual.section_links(data)}
        _awaiting_hp.pop(uid, None)
        rep = rebuild(uid)
        _stats["reports"] += 1
        tk = await get_turnkey(rep, uid)
        await send_photos(message, rep)
        await message.answer(texts.card(rep, tk), reply_markup=keyboard(uid, rep),
                             disable_web_page_preview=True)
        if s["links"]:
            await message.answer(texts.next_steps(s["links"]), disable_web_page_preview=True)
        return

    if not s or not s.get("vehicle"):
        await message.answer("Сначала пришлите «данные авто» — ссылку на объявление, "
                             "затем ответ страницы, которую даст бот.")
        return
    s[kind] = data
    rep = rebuild(uid)
    if kind == "record":
        r = await get_rates()
        await message.answer(texts.history(rep, r["KRW"]))
    else:
        await message.answer(texts.inspection(rep))
    tk = await get_turnkey(rep, uid)
    left = [k for k in s["links"] if not s.get(k)]
    note = ("\n\nОсталось прислать: " + ", ".join(SECTION_NAMES[k] for k in left)) if left else \
           "\n\n✅ Все разделы получены."
    await message.answer("<b>Итог обновлён:</b>\n" + "\n".join(texts.verdict(rep))
                         + (f"\n\n💰 Под ключ ≈ {texts.rub(tk.total_rub)}" if tk.ok else "") + note,
                         reply_markup=keyboard(uid, rep))


@router.message(F.document)
async def msg_document(message: Message, bot: Bot):
    doc = message.document
    if doc.file_size and doc.file_size > 3_000_000:
        return await message.answer("Файл слишком большой. Нужен ответ страницы Encar (обычно до 50 КБ).")
    buf = await bot.download(doc)
    raw = buf.read()
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp949", "cp1251"):
        try:
            text = raw.decode(enc)
            if "{" in text:
                break
        except UnicodeDecodeError:
            continue
    data = manual.extract_json(text or "")
    if data is None:
        return await message.answer("В файле не нашёл данных Encar. Сохраните страницу заново "
                                    "(Ctrl+S) и пришлите файл.")
    await handle_payload(message, data)


def _is_json_chunk(message: Message) -> bool:
    t = (message.text or "").lstrip()
    if t.startswith(("{", "JBID")):
        return True
    if message.from_user.id in _buffers:
        if t.startswith(("/", "http")) or extract_car_id(t):   # пришла новая ссылка — бросаем склейку
            _buffers.pop(message.from_user.id, None)
            return False
        return True
    return False


@router.message(F.text.func(lambda t: bool(t)) & F.func(_is_json_chunk))
async def msg_json_text(message: Message):
    """Длинный текст Telegram режет на части по 4096 символов — склеиваем."""
    uid = message.from_user.id
    started, acc = _buffers.get(uid, (time.time(), ""))
    if time.time() - started > 120:
        acc = ""
    acc += message.text
    data = manual.extract_json(acc)
    if data is None:
        if len(acc) > 500_000:
            _buffers.pop(uid, None)
            return await message.answer("Не получилось собрать данные. Пришлите их файлом.")
        _buffers[uid] = (started, acc)
        return                                            # ждём следующую часть
    _buffers.pop(uid, None)
    await handle_payload(message, data)


# ── Мощность ────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("hp:"))
async def cb_hp(call: CallbackQuery):
    _awaiting_hp[call.from_user.id] = call.data.split(":", 1)[1]
    await call.message.answer("⚡ Напишите мощность двигателя в л.с. (например, <code>180</code>).\n"
                              "Для гибрида — суммарную (ДВС + электромотор).")
    await call.answer()


@router.message(F.text.regexp(r"^\s*\d{2,4}\s*(л\.?\s*с\.?|hp|лс)?\s*$"))
async def msg_hp(message: Message):
    uid = message.from_user.id
    car_id = _awaiting_hp.get(uid)
    rep = _reports.get(car_id) if car_id else None
    if not rep:
        return await msg_any(message)
    hp = int("".join(ch for ch in message.text if ch.isdigit()))
    if not 40 <= hp <= 1500:
        return await message.answer("Похоже на опечатку — введите мощность от 40 до 1500 л.с.")
    _awaiting_hp.pop(uid, None)
    _hp[(uid, car_id)] = hp
    tk = await get_turnkey(rep, uid)
    await message.answer(texts.cost(tk, rep), reply_markup=keyboard(uid, rep))


# ── Ссылка → инструкция ─────────────────────────────────────────────────────
@router.message(F.text)
async def msg_any(message: Message):
    car_id = extract_car_id(message.text or "")
    if not car_id:
        return await message.answer(
            "Пришлите ссылку на объявление Encar или его номер.\n"
            "Пример: <code>https://fem.encar.com/cars/detail/42519941</code>")
    uid = message.from_user.id
    _sessions[uid] = {"car_id": car_id}
    _awaiting_hp.pop(uid, None)
    _buffers.pop(uid, None)
    kb = InlineKeyboardMarkup(inline_keyboard=[[open_button("vehicle", manual.vehicle_url(car_id))]])
    await message.answer(texts.how_to_send(car_id, manual.vehicle_url(car_id)), reply_markup=kb,
                         disable_web_page_preview=True)


# ── Кнопки под отчётом ──────────────────────────────────────────────────────
def _report(call: CallbackQuery) -> CarReport | None:
    return _reports.get(call.data.split(":", 1)[1])


async def _expired(call: CallbackQuery):
    await call.answer("Отчёт устарел — пришлите ссылку на объявление заново", show_alert=True)


async def _need_section(call: CallbackQuery, kind: str) -> bool:
    """Если раздел ещё не прислан — дать ссылку на него."""
    s = _sessions.get(call.from_user.id, {})
    url = s.get("links", {}).get(kind)
    if s.get(kind) or not url:
        return False
    kb = InlineKeyboardMarkup(inline_keyboard=[[open_button(kind, url)]])
    await call.message.answer(texts.link_block(f"Ссылка на {SECTION_NAMES[kind]}", url) + "\n\n"
                              + texts.HOW_TO, reply_markup=kb, disable_web_page_preview=True)
    await call.answer()
    return True


@router.callback_query(F.data.startswith("h:"))
async def cb_history(call: CallbackQuery):
    rep = _report(call)
    if not rep:
        return await _expired(call)
    if await _need_section(call, "record"):
        return
    r = await get_rates()
    await call.message.answer(texts.history(rep, r["KRW"]))
    await call.answer()


@router.callback_query(F.data.startswith("i:"))
async def cb_inspection(call: CallbackQuery):
    rep = _report(call)
    if not rep:
        return await _expired(call)
    if await _need_section(call, "inspection"):
        return
    await call.message.answer(texts.inspection(rep))
    await call.answer()


@router.callback_query(F.data.startswith("c:"))
async def cb_cost(call: CallbackQuery):
    rep = _report(call)
    if not rep:
        return await _expired(call)
    await call.message.answer(texts.cost(await get_turnkey(rep, call.from_user.id), rep))
    await call.answer()


@router.callback_query(F.data.startswith("ph:"))
async def cb_photos(call: CallbackQuery):
    rep = _report(call)
    if not rep:
        return await _expired(call)
    await call.answer("Загружаю фото…")
    for start in range(config.MAX_PHOTOS_IN_ALBUM, min(len(rep.photos), 40), config.MAX_PHOTOS_IN_ALBUM):
        await send_photos(call.message, rep, start)


@router.callback_query(F.data.startswith("lead:"))
async def cb_lead(call: CallbackQuery, bot: Bot):
    rep = _report(call)
    if not rep:
        return await _expired(call)
    u = call.from_user
    who = f"@{u.username}" if u.username else f"id {u.id}"
    tk = await get_turnkey(rep, u.id)
    total = texts.rub(tk.total_rub) if tk.ok else "ручной расчёт"
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
    log.info("Бот запущен (ручной режим: сервер на Encar не обращается)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
