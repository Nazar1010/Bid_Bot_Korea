"""Курсы ЦБ РФ: ₽ за 1 KRW / EUR / USD с кэшем."""
import logging
import time
import xml.etree.ElementTree as ET

import httpx

import config

log = logging.getLogger(__name__)

_cache: dict = {"t": 0.0, "rates": None, "date": None, "source": None}


def _parse_json(data: dict) -> dict:
    out = {}
    for code in ("KRW", "EUR", "USD"):
        v = data["Valute"][code]
        out[code] = float(v["Value"]) / float(v["Nominal"])
    return out


def _parse_xml(text: str) -> tuple[dict, str]:
    root = ET.fromstring(text)
    out = {}
    for v in root.findall("Valute"):
        code = v.findtext("CharCode")
        if code in ("KRW", "EUR", "USD"):
            val = float(v.findtext("Value").replace(",", "."))
            out[code] = val / float(v.findtext("Nominal"))
    return out, root.attrib.get("Date", "")


async def get_rates() -> dict:
    """{'KRW': ..., 'EUR': ..., 'USD': ..., 'date': '05.11.2026', 'source': 'ЦБ РФ'}"""
    if _cache["rates"] and time.time() - _cache["t"] < config.RATES_CACHE_MINUTES * 60:
        return {**_cache["rates"], "date": _cache["date"], "source": _cache["source"]}
    async with httpx.AsyncClient(timeout=10) as http:
        try:
            r = await http.get("https://www.cbr-xml-daily.ru/daily_json.js")
            data = r.json()
            rates = _parse_json(data)
            date = str(data.get("Date", ""))[:10]
            date = ".".join(reversed(date.split("-"))) if "-" in date else date
            _cache.update(t=time.time(), rates=rates, date=date, source="ЦБ РФ")
        except Exception as e:  # noqa: BLE001
            log.warning("cbr-xml-daily: %s", e)
            try:
                r = await http.get("https://www.cbr.ru/scripts/XML_daily.asp")
                rates, date = _parse_xml(r.content.decode("windows-1251"))
                if len(rates) == 3:
                    _cache.update(t=time.time(), rates=rates, date=date, source="ЦБ РФ")
            except Exception as e2:  # noqa: BLE001
                log.error("cbr.ru: %s", e2)
    if not _cache["rates"]:
        return {**config.FALLBACK_RATES, "date": "—", "source": "запасные (ЦБ недоступен)"}
    return {**_cache["rates"], "date": _cache["date"], "source": _cache["source"]}
