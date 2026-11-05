"""Тексты сообщений (HTML-разметка Telegram)."""
from html import escape

import config
from calc.turnkey import Turnkey
from encar.parser import CarReport


def rub(x: float) -> str:
    return f"{round(x):,}".replace(",", " ") + " ₽"


def krw(x: float) -> str:
    return f"{round(x):,}".replace(",", " ") + " ₩"


def num(x) -> str:
    return f"{round(x):,}".replace(",", " ")


def e(x) -> str:
    return escape(str(x)) if x is not None else ""


# ── Короткий вердикт по истории ─────────────────────────────────────────────
def verdict(rep: CarReport) -> list[str]:
    h, ins, dg = rep.history, rep.inspection, rep.diagnosis
    out = []
    if h.total_loss or h.flood_total or h.flood_part or ins.flood:
        out.append("🚨 <b>Тотал / утопление в истории</b>")
    if h.theft:
        out.append("🚨 <b>Был в угоне</b>")
    frame = [p for p in ins.panels if p.frame]
    if frame:
        out.append(f"🔴 Повреждения <b>каркаса</b> по техосмотру: {len(frame)}")
    if ins.accident is True and not frame:
        out.append("🟠 Техосмотр: отмечено ДТП")
    if h.available:
        if h.own_cnt or h.other_cnt:
            out.append(f"🟠 Страховых случаев: {h.total_cases} "
                       f"(ущерб этому авто: {h.own_cnt} на {krw(h.own_cost)})")
        else:
            out.append("🟢 Страховых случаев нет")
        if h.rent:
            out.append("🟠 Был в прокате (렌트)")
        if h.business:
            out.append("🟠 Коммерческое использование")
    elif h.hidden_by_seller:
        out.append("⚪ Страховая история скрыта продавцом")
    else:
        out.append("⚪ Страховая история недоступна")
    if ins.available and ins.accident is False and not frame:
        out.append("🟢 Техосмотр: ДТП нет, каркас цел")
    replaced = [p for p in ins.panels if not p.frame]
    if replaced:
        out.append(f"🟡 Навесные панели: {len(replaced)} (замена/ремонт)")
    if not ins.available:
        out.append("⚪ Техосмотр не выложен")
    if dg.available and dg.no_accident_verdict and not dg.issues:
        out.append("🟢 Диагностика Encar: без ДТП")
    return out


def card(rep: CarReport, tk: Turnkey | None) -> str:
    lines = [f"🚗 <b>{e(rep.title)}</b>"]
    if rep.grade:
        lines.append(f"<i>{e(rep.grade)}</i>")
    lines.append("")
    spec = []
    spec.append(f"📅 Выпуск: <b>{e(rep.year_month)}</b>" +
                (f" (модельный год {e(rep.model_year)})" if rep.model_year and rep.model_year != str(rep.prod_year) else ""))
    spec.append(f"🛣 Пробег: <b>{num(rep.mileage)} км</b>")
    eng = []
    if rep.displacement:
        eng.append(f"{rep.displacement} см³")
    if rep.fuel:
        eng.append(rep.fuel)
    if rep.transmission:
        eng.append(rep.transmission)
    if eng:
        spec.append("⚙️ " + e(", ".join(eng)))
    extra = ", ".join(x for x in (rep.body, rep.color, f"{rep.seats} мест" if rep.seats else "") if x)
    if extra:
        spec.append("🎨 " + e(extra))
    if rep.vin:
        spec.append(f"🔢 VIN: <code>{e(rep.vin)}</code>")
    lines += spec
    lines.append("")
    price = f"💴 Цена в Корее: <b>{krw(rep.price_krw)}</b>"
    if tk and tk.rates:
        price += f" (≈ {rub(rep.price_krw * tk.rates['KRW'])})"
    lines.append(price)
    if rep.new_price_krw and rep.new_price_krw > rep.price_krw:
        lines.append(f"🏷 Новый стоил: {krw(rep.new_price_krw)}")
    if rep.warranty:
        lines.append(f"🛡 Гарантия: {e(rep.warranty)}")
    if config.SHOW_OPTIONS and rep.options:
        lines.append("✨ Опции: " + e(", ".join(rep.options)))
    lines.append("")
    lines.append("<b>История:</b>")
    lines += verdict(rep)
    lines.append("")
    if tk and tk.ok:
        lines.append(f"💰 <b>Под ключ во Владивостоке: ≈ {rub(tk.total_rub)}</b>")
        lines.append(f"🚛 С доставкой до Москвы: ≈ {rub(tk.moscow_rub)}")
        if tk.sanctioned:
            lines.append("⚠️ Санкционный объём (>2 л) — через третью страну, см. расчёт")
    elif tk:
        lines.append(f"💰 {e(tk.reason)}")
    lines.append(f'\n<a href="{e(rep.url)}">Объявление на Encar</a> · №{e(rep.car_id)}')
    return "\n".join(lines)


def cost(tk: Turnkey, rep: CarReport) -> str:
    if not tk.ok:
        return f"💰 {e(tk.reason)}"
    c = tk.customs
    r = tk.rates
    L = [f"💰 <b>Расчёт под ключ — {e(rep.title)}</b>", ""]
    L.append(f"Авто в Корее: {krw(tk.car_krw)} → <b>{rub(tk.car_rub)}</b>")
    L.append(f"Расходы по Корее (документы, порт): {rub(tk.korea_rub)}")
    L.append(f"Фрахт Пусан → Владивосток: {rub(tk.freight_rub)}")
    if tk.sanctioned:
        L.append(f"Маршрут через третью страну: {rub(tk.sanction_rub)} (ориентир)")
    L.append("")
    L.append(f"<b>Таможня ({e(c.age_group)} на {tk.decl_date:%m.%Y}):</b>")
    L.append(f"  • пошлина: {rub(c.duty_rub)} ({num(c.duty_eur)} €, {e(c.duty_rule)})")
    L.append(f"  • утильсбор: {rub(c.util_rub)} — {e(c.util_rule)}")
    L.append(f"  • сбор за оформление: {rub(c.fee_rub)}")
    L.append(f"  Итого таможня: <b>{rub(c.total)}</b>")
    L.append("")
    L.append(f"Владивосток (СВХ, брокер, СБКТС/ЭПТС): {rub(tk.vladivostok_rub)}")
    L.append(f"Услуги J-Bid: {rub(tk.commission_rub)}")
    L.append("")
    L.append(f"✅ <b>ИТОГО во Владивостоке: {rub(tk.total_rub)}</b>")
    L.append(f"🚛 + автовоз до Москвы: {rub(tk.moscow_rub)}")
    L.append("")
    L.append(f"<i>Курс ЦБ на {e(r.get('date'))}: 1000 ₩ = {r['KRW'] * 1000:.2f} ₽, "
             f"€ = {r['EUR']:.2f} ₽, $ = {r['USD']:.2f} ₽ ({e(r.get('source'))}). "
             f"Перевод в Корею с комиссией {config.TRANSFER_FEE_PCT:g}%.</i>")
    if tk.hp:
        L.append(f"<i>Мощность: {tk.hp} л.с.</i>")
    for w in tk.warnings:
        L.append(f"⚠️ {e(w)}")
    L.append("<i>Расчёт предварительный; точную сумму подтвердит менеджер.</i>")
    return "\n".join(L)


def history(rep: CarReport, krw_rub: float | None = None) -> str:
    h = rep.history

    def m(x):
        return krw(x) + (f" (≈ {rub(x * krw_rub)})" if krw_rub and x else "")

    L = [f"📋 <b>Страховая история — {e(rep.title)}</b>", ""]
    if not h.available:
        L.append("Продавец скрыл страховую историю." if h.hidden_by_seller
                 else "Страховая история по этому авто недоступна.")
        return "\n".join(L)
    if h.first_registration:
        L.append(f"Первая регистрация: {e(h.first_registration)}")
    L.append(f"Смен владельцев: <b>{h.owner_changes if h.owner_changes is not None else '—'}</b>"
             + (f" ({', '.join(e(d) for d in h.owner_change_dates)})" if h.owner_change_dates else ""))
    if h.plate_changes:
        L.append(f"Смен госномера: {h.plate_changes}")
    use = []
    if h.rent:
        use.append("прокат (렌트)")
    if h.business:
        use.append("коммерческое")
    if h.government:
        use.append("госорганы")
    L.append("Особое использование: " + (", ".join(use) if use else "нет"))
    L.append("")
    L.append(f"<b>Страховых случаев: {h.total_cases}</b>")
    L.append(f"• Ущерб этому авто: {h.own_cnt} шт., {m(h.own_cost)}")
    L.append(f"• Ущерб, причинённый другим: {h.other_cnt} шт., {m(h.other_cost)}")
    if h.accidents:
        L.append("")
        L.append("<b>Случаи:</b>")
        for a in sorted(h.accidents, key=lambda a: a.date[-4:] + a.date[3:5] + a.date[:2]):
            parts = []
            if a.benefit:
                parts.append(f"выплата {m(a.benefit)}")
            if a.repair_total:
                parts.append(f"ремонт {krw(a.repair_total)} (запчасти {num(a.parts or 0)}, "
                             f"работа {num(a.labor or 0)}, покраска {num(a.paint or 0)})")
            L.append(f"— {e(a.date)}: " + ("; ".join(parts) if parts else "сумма не указана"))
    L.append("")
    L.append(f"Тотал: {'🚨 ' + str(h.total_loss) + (' (' + h.total_loss_date + ')' if h.total_loss_date else '') if h.total_loss else 'нет'}")
    flood = h.flood_total + h.flood_part
    L.append(f"Утопление: {'🚨 полное ' + str(h.flood_total) + ', частичное ' + str(h.flood_part) if flood else 'нет'}")
    L.append(f"Угон: {'🚨 ' + str(h.theft) if h.theft else 'нет'}")
    if h.no_insurance_periods:
        L.append("")
        L.append("⚠️ Периоды без данных о страховке (ДТП за это время не видны):")
        for p in h.no_insurance_periods:
            L.append(f"  • {e(p)}")
    return "\n".join(L)


def inspection(rep: CarReport) -> str:
    ins, dg = rep.inspection, rep.diagnosis
    L = [f"🔧 <b>Техосмотр и диагностика — {e(rep.title)}</b>", ""]
    if not ins.available and not dg.available:
        L.append("Продавец не выложил техосмотр.")
        return "\n".join(L)
    if ins.available:
        L.append(f"<b>Техосмотр (성능점검)</b>{' от ' + e(ins.date) if ins.date else ''}")
        if ins.accident is not None:
            L.append("ДТП: " + ("🔴 да" if ins.accident else "🟢 нет"))
        if ins.simple_repair:
            L.append("Простой ремонт (без ДТП): да")
        if ins.flood:
            L.append("🚨 Следы затопления")
        if ins.by_dealer:
            L.append("<i>Заполнен продавцом/дилером, не сервисом Encar.</i>")
        if ins.panels:
            L.append("")
            L.append("Кузов:")
            for p in ins.panels:
                mark = "🔴" if p.frame else "🟡"
                rank = f" [{e(p.rank)}]" if p.rank else ""
                L.append(f"{mark} {e(p.part)} — {e(p.status)}{rank}")
        else:
            L.append("Кузов: замен и ремонта панелей нет")
        if ins.unit_problems:
            L.append("")
            L.append("Агрегаты (замечания):")
            for u in ins.unit_problems[:20]:
                L.append(f"⚠️ {e(u)}")
        elif ins.available:
            L.append("Агрегаты: замечаний нет")
    if dg.available:
        L.append("")
        L.append("<b>Диагностика Encar (엔카진단)</b>")
        if dg.issues:
            for i in dg.issues:
                L.append(f"🟡 {e(i)}")
        else:
            L.append("🟢 " + ("без ДТП" if dg.no_accident_verdict else "замечаний нет"))
    return "\n".join(L)


def welcome() -> str:
    return (
        "👋 Это бот <b>J-Bid</b> по авто из Южной Кореи.\n\n"
        "Пришлите ссылку на объявление <b>Encar</b> (или его номер). Я дам ссылку на данные — "
        "откройте её в браузере и перешлите ответ мне. Покажу:\n"
        "• марку, модель, год, пробег, двигатель, фото;\n"
        "• страховую историю: ДТП, выплаты, тотал, утопление, угон, владельцы;\n"
        "• техосмотр: какие детали меняли и красили, состояние агрегатов;\n"
        "• <b>полную стоимость под ключ</b> во Владивостоке и до Москвы.\n\n"
        "Пример: <code>https://fem.encar.com/cars/detail/42519941</code>\n\n"
        f"Менеджер: @{config.MANAGER_USERNAME} · {config.MANAGER_PHONE}"
    )


HOW_TO = ("1) Нажмите на ссылку выше — она скопируется.\n"
          "2) Вставьте её в адресную строку браузера и откройте "
          "(будет сплошной текст в фигурных скобках — так и должно быть).\n"
          "3) Выделите всё (<b>Ctrl+A</b>), скопируйте (<b>Ctrl+C</b>) и вставьте сюда. "
          "Длинный текст Telegram разобьёт на части — я их склею.\n"
          "<i>Можно и файлом: Ctrl+S в браузере → перетащить файл сюда.</i>")


def link_block(title: str, url: str) -> str:
    return f"{title}:\n<code>{e(url)}</code>"


def how_to_send(car_id: str, url: str) -> str:
    return (f"🚗 Объявление №{e(car_id)}\n\n" + link_block("Ссылка на данные авто", url)
            + "\n\n" + HOW_TO)


def next_steps(links: dict) -> str:
    names = {"record": "📋 Страховая история", "inspection": "🔧 Техосмотр",
             "diagnosis": "🩺 Диагностика Encar"}
    blocks = [link_block(names[k], u) for k, u in links.items() if k in names]
    return ("Для полной проверки откройте так же эти ссылки и вставьте ответы сюда — по одному:\n\n"
            + "\n\n".join(blocks))