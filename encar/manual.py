"""Ручной режим: ответ Encar открывается в браузере и пересылается боту.

Бот сам на Encar не ходит. Он только строит ссылки на нужные страницы
API и разбирает присланный текст или файл.
"""
import html
import json
from urllib.parse import quote

API = "https://api.encar.com/v1/readside"


def vehicle_url(car_id) -> str:
    return f"{API}/vehicle/{car_id}"


def record_url(vehicle_id, vehicle_no) -> str | None:
    if not vehicle_no:
        return None
    return f"{API}/record/vehicle/{vehicle_id}/open?vehicleNo={quote(str(vehicle_no))}"


def inspection_url(vehicle_id) -> str:
    return f"{API}/inspection/vehicle/{vehicle_id}"


def diagnosis_url(vehicle_id) -> str:
    return f"{API}/diagnosis/vehicle/{vehicle_id}"


def extract_json(text: str):
    """Достаёт JSON из присланного текста (в т.ч. из сохранённой HTML-страницы)."""
    if not text:
        return None
    s = text.strip().lstrip("﻿")
    if "<" in s and ("<pre" in s or "<html" in s.lower()):
        s = html.unescape(s)
    start = min((i for i in (s.find("{"), s.find("[")) if i >= 0), default=-1)
    end = max(s.rfind("}"), s.rfind("]"))
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except ValueError:
        return None


def detect_kind(data) -> str | None:
    """vehicle / record / inspection / diagnosis — по набору полей."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("vehicle"), dict) and "car_id" in data:
        return "bundle"                       # всё сразу — из закладки J-Bid
    if "category" in data and ("spec" in data or "advertisement" in data):
        return "vehicle"
    if any(k in data for k in ("myAccidentCnt", "otherAccidentCnt", "accidentCnt", "openData")):
        return "record"
    if "master" in data and ("outers" in data or "inners" in data):
        return "inspection"
    if isinstance(data.get("items"), list):
        return "diagnosis"
    return None


def section_links(vehicle: dict) -> dict:
    """Какие разделы есть у объявления и ссылки на них."""
    vid = vehicle.get("vehicleId")
    no = vehicle.get("vehicleNo") or (vehicle.get("manage") or {}).get("vehicleNo")
    cond = vehicle.get("condition") or {}
    adv = vehicle.get("advertisement") or {}
    links = {}
    if (cond.get("accident") or {}).get("recordView") is not False:
        u = record_url(vid, no)
        if u:
            links["record"] = u
    if (cond.get("inspection") or {}).get("formats"):
        links["inspection"] = inspection_url(vid)
    if adv.get("diagnosisCar") or (vehicle.get("view") or {}).get("encarDiagnosis", -1) >= 0:
        links["diagnosis"] = diagnosis_url(vid)
    return links
