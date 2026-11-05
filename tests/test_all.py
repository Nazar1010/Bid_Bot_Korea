import copy
import json
from datetime import date
from pathlib import Path

import pytest

import texts
from calc import customs, turnkey
from encar.client import extract_car_id, photo_url
from encar.dictionaries import translate_part
from encar.parser import build_report

RAW = json.loads((Path(__file__).parent / "fixtures" / "sample.json").read_text(encoding="utf-8"))
RATES = {"KRW": 0.06, "EUR": 95.0, "USD": 82.0, "date": "05.11.2026", "source": "тест"}


def test_extract_id():
    assert extract_car_id("https://fem.encar.com/cars/detail/41000001?pageid=x") == "41000001"
    assert extract_car_id("http://www.encar.com/dc/dc_cardetailview.do?pageid=dc&carid=38020354&x=1") == "38020354"
    assert extract_car_id(" 41000001 ") == "41000001"
    assert extract_car_id("привет") is None
    assert extract_car_id("180") is None


def test_photo_url():
    assert photo_url("/carpicture07/pic4099/1_001.jpg").startswith(
        "https://ci.encar.com/carpicture/carpicture07/pic4099/1_001.jpg?")


def test_parts():
    assert translate_part("프론트 펜더(좌)") == "переднее крыло (лев.)"
    assert translate_part("도어(앞,우)") == "дверь (пер., прав.)"


def test_report():
    r = build_report(RAW)
    assert r.title == "Hyundai Sonata (DN8)"
    assert r.grade == "1.6 Turbo Inspiration"
    assert r.year_month == "03.2022"
    assert r.price_krw == 21_500_000 and r.new_price_krw == 35_000_000
    assert r.displacement == 1598 and r.fuel == "бензин" and r.transmission == "автомат"
    assert r.color == "белый" and r.region == "Кёнгидо"
    assert len(r.photos) == 2          # дубль и фото чека отброшены
    h = r.history
    assert h.available and h.total_cases == 2 and h.own_cost == 1_850_000
    assert h.owner_changes == 1 and h.plate_changes == 1
    assert h.no_insurance_periods == ["01.2023 – 05.2023"]
    assert h.accidents[0].repair_total == 1_850_000
    ins = r.inspection
    assert ins.accident is False and ins.simple_repair and len(ins.panels) == 2
    assert any("течь масла" in u and "микроподтекание" in u for u in ins.unit_problems)
    assert len(ins.unit_problems) == 1     # «нет» по тормозам — норма
    assert r.diagnosis.no_accident_verdict and r.diagnosis.issues == ["переднее левое крыло — замена"]


def test_hidden_record():
    raw = copy.deepcopy(RAW)
    raw["record"], raw["record_hidden"] = None, True
    r = build_report(raw)
    assert not r.history.available and r.history.hidden_by_seller
    assert "скрыл" in texts.history(r)


def test_duty_rules():
    # 3–5 лет, 1598 см³ → 2,5 €/см³
    d, rule = customs.duty(20_000, 1598, 4.0)
    assert d == pytest.approx(3995) and "2.5" in rule
    # до 3 лет, 30 000 € → max(48% = 14 400, 5,5×1998 = 10 989)
    d, _ = customs.duty(30_000, 1998, 2.0)
    assert d == pytest.approx(14_400)
    # старше 5 лет, 2497 см³ → 5 €/см³
    assert customs.duty(10_000, 2497, 7)[0] == pytest.approx(12_485)
    assert customs.clearance_fee(1_290_000) == 13_541


def test_util():
    assert customs.util_fee(1598, None, 4, 2026)[0] == 5_200
    assert customs.util_fee(1598, 150, 2, 2026)[0] == 3_400
    assert customs.util_fee(1998, 180, 4, 2026)[0] == 1_492_800
    assert customs.util_fee(2497, 200, 2, 2027)[0] == 2_600_400
    assert customs.util_fee(3342, 150, 4, 2026)[0] == 3_956_200


def test_turnkey():
    r = build_report(RAW)
    t = turnkey.calculate(r, RATES, today=date(2026, 11, 5))
    assert t.ok and not t.sanctioned
    assert t.customs.age_group == "3–5 лет"
    car = 21_500_000 * 0.06 * 1.02
    assert t.car_rub == pytest.approx(car)
    assert t.total_rub > car
    text = texts.cost(t, r)
    assert "ИТОГО" in text and "пошлина" in text


def test_sanctioned_and_ev():
    raw = copy.deepcopy(RAW)
    raw["vehicle"]["spec"]["displacement"] = 2497
    t = turnkey.calculate(build_report(raw), RATES, today=date(2026, 11, 5))
    assert t.ok and t.sanctioned and t.sanction_rub > 0
    raw["vehicle"]["spec"]["fuelName"] = "전기"
    assert not turnkey.calculate(build_report(raw), RATES).ok


def test_card_length():
    r = build_report(RAW)
    t = turnkey.calculate(r, RATES, today=date(2026, 11, 5))
    for s in (texts.card(r, t), texts.history(r, 0.06), texts.inspection(r), texts.cost(t, r)):
        assert len(s) < 4096


def test_client_flow():
    import asyncio
    import httpx
    from encar.client import EncarClient
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        p = req.url.path
        if p.startswith("/v1/readside/vehicle/"):
            return httpx.Response(200, json=RAW["vehicle"])
        if "/record/" in p:
            assert req.url.params["vehicleNo"] == "12가3456"
            return httpx.Response(200, json=RAW["record"])
        if "/inspection/" in p:
            return httpx.Response(200, json=RAW["inspection"])
        return httpx.Response(404)

    async def run():
        c = EncarClient()
        c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        raw = await c.fetch_all("41000001")
        await c.close()
        return raw

    raw = asyncio.run(run())
    # история запрашивается по внутреннему vehicleId, а не по номеру из ссылки
    assert "/v1/readside/record/vehicle/40990002/open" in seen
    assert raw["diagnosis"] is None and raw["record"]["myAccidentCnt"] == 1
    r = build_report(raw)
    assert r.history.available and r.inspection.available


def test_bot_imports():
    import bot  # noqa: F401
    kb = bot.keyboard("41000001", 25)
    assert any("Ещё фото" in b.text for row in kb.inline_keyboard for b in row)
