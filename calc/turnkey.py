"""Полная стоимость «под ключ» во Владивостоке (и опционально до Москвы)."""
from dataclasses import dataclass, field
from datetime import date, timedelta

import config
from calc import customs as cust
from encar.parser import CarReport, age_years


@dataclass
class Turnkey:
    ok: bool
    reason: str = ""
    car_rub: float = 0
    car_krw: int = 0
    korea_rub: float = 0
    freight_rub: float = 0
    sanction_rub: float = 0
    customs: cust.CustomsResult | None = None
    vladivostok_rub: float = 0
    commission_rub: float = 0
    total_rub: float = 0
    moscow_rub: float = 0
    age: float | None = None
    decl_date: date | None = None
    sanctioned: bool = False
    warnings: list[str] = field(default_factory=list)
    rates: dict = field(default_factory=dict)
    hp: int | None = None


def calculate(rep: CarReport, rates: dict, hp: int | None = None, today: date | None = None) -> Turnkey:
    today = today or date.today()
    t = Turnkey(ok=False, rates=rates, hp=hp)
    if rep.is_electric:
        t.reason = ("Электромобиль: для физлиц считается по особым правилам (15% стоимости, "
                    "акциз по мощности, НДС 22%). Рассчитаем вручную — оставьте заявку.")
        return t
    if not rep.displacement:
        t.reason = "В объявлении не указан объём двигателя — рассчитаем вручную."
        return t
    if not rep.price_krw:
        t.reason = "В объявлении нет цены."
        return t
    if rep.lease_or_rent_ad:
        t.warnings.append("Объявление о лизинге/аренде: цена может быть взносом, а не стоимостью авто.")

    krw = rates["KRW"] * (1 + config.TRANSFER_FEE_PCT / 100)
    t.car_krw = rep.price_krw
    t.car_rub = rep.price_krw * krw
    t.korea_rub = config.KOREA_EXPENSES_KRW * krw
    t.freight_rub = config.FREIGHT_USD * rates["USD"]

    t.sanctioned = rep.displacement > config.KOREA_EXPORT_LIMIT_CC
    if t.sanctioned:
        t.sanction_rub = config.SANCTION_EXTRA_USD * rates["USD"]
        t.warnings.append(
            f"Объём {rep.displacement} см³ > 2000 см³: Корея запрещает прямой экспорт таких авто в РФ. "
            f"Только через третью страну, срок {config.SANCTION_TERM_TEXT}, доставка считается отдельно.")

    term = config.DELIVERY_DAYS + (60 if t.sanctioned else 0)
    t.decl_date = today + timedelta(days=term)
    t.age = age_years(rep, t.decl_date)
    if t.age is None:
        t.reason = "Не указан год выпуска — рассчитаем вручную."
        return t

    # Таможенная стоимость — цена авто по курсу ЦБ (без наценки на перевод)
    t.customs = cust.calc(rep.price_krw, rates["KRW"], rates["EUR"], rep.displacement,
                             t.age, hp, t.decl_date.year)
    if rep.is_hybrid:
        t.warnings.append("Гибрид: для утильсбора мощность ДВС и электромотора складывается.")
    if hp is None and rep.displacement <= 3000:
        t.warnings.append("Мощность в Корее не указывается. Утильсбор посчитан по льготе (≤160 л.с.) — "
                          "если мощнее, нажмите «Указать мощность».")
    # Граница возрастной категории — предупредить
    for border in (3, 5):
        if abs(t.age - border) < 0.25:
            t.warnings.append(f"Авто на границе {border} лет — пошлина зависит от даты растаможки.")

    t.vladivostok_rub = config.VLADIVOSTOK_EXPENSES_RUB
    t.commission_rub = config.COMPANY_COMMISSION_RUB
    t.total_rub = (t.car_rub + t.korea_rub + t.freight_rub + t.sanction_rub + t.customs.total
                   + t.vladivostok_rub + t.commission_rub)
    t.moscow_rub = t.total_rub + config.MOSCOW_DELIVERY_RUB
    t.ok = True
    return t
