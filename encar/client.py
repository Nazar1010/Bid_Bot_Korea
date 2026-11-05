"""HTTP-клиент к внутреннему JSON API Encar.

Эндпоинты (анонимные, их вызывает сам сайт fem.encar.com):
  vehicle     /v1/readside/vehicle/{carId}            карточка: марка, год, пробег, фото, VIN
  record      /v1/readside/record/vehicle/{vid}/open  страховая история (нужен госномер)
  inspection  /v1/readside/inspection/vehicle/{vid}   техосмотр 성능점검기록부
  diagnosis   /v1/readside/diagnosis/vehicle/{vid}    диагностика Encar (엔카진단)

Важно: страховая, техосмотр и диагностика живут под внутренним vehicleId,
который берётся из карточки (у перевыставленных объявлений он отличается
от номера в ссылке).
"""
import asyncio
import logging
import re
import time
from urllib.parse import quote

import httpx

import config

log = logging.getLogger(__name__)

API = "https://api.encar.com"
VEHICLE_INCLUDE = ("ADVERTISEMENT,CATEGORY,CONDITION,CONTACT,MANAGE,OPTIONS,"
                   "PHOTOS,SPEC,PARTNERSHIP,CENTER,VIEW")
PHOTO_BASE = "https://ci.encar.com/carpicture"
PHOTO_FALLBACK_BASE = "https://imgcar.encar.com"
PHOTO_SIZE = "?impolicy=heightRate&rh=720&cw=1080&ch=720&cg=Center"
DETAIL_PAGE = "https://fem.encar.com/cars/detail/{}"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://fem.encar.com/",
    "Origin": "https://fem.encar.com",
}

_ID_PATTERNS = [
    re.compile(r"cars/detail/(\d{6,10})"),
    re.compile(r"[?&]carid=(\d{6,10})", re.I),
    re.compile(r"[?&]carId=(\d{6,10})"),
    re.compile(r"encar\.com\S*?/(\d{7,10})(?:\D|$)"),
]


class EncarError(Exception):
    """Encar недоступен или вернул что-то непонятное."""


class CarNotFound(EncarError):
    """Объявление снято или номер неверный."""


def extract_car_id(text: str) -> str | None:
    """Номер объявления из ссылки Encar или из «голого» числа."""
    if not text:
        return None
    text = text.strip()
    for p in _ID_PATTERNS:
        m = p.search(text)
        if m:
            return m.group(1)
    m = re.fullmatch(r"\s*(\d{7,10})\s*", text)
    return m.group(1) if m else None


def photo_url(path: str) -> str:
    """Путь фото из API → ссылка на CDN (без водяного знака Encar)."""
    p = "/" + str(path).lstrip("/")
    return f"{PHOTO_BASE}{p}{PHOTO_SIZE}"


def photo_fallback_url(path: str) -> str:
    return f"{PHOTO_FALLBACK_BASE}/{str(path).lstrip('/')}"


class EncarClient:
    def __init__(self):
        kwargs = dict(headers=HEADERS, timeout=config.ENCAR_TIMEOUT, follow_redirects=True)
        if config.ENCAR_PROXY:
            kwargs["proxy"] = config.ENCAR_PROXY
        self._http = httpx.AsyncClient(**kwargs)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def close(self):
        await self._http.aclose()

    async def _throttle(self):
        async with self._lock:
            wait = self._last + config.ENCAR_MIN_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    async def _get_json(self, url: str, *, required: bool = False):
        """GET с повторами. 404 → None (у объявления нет этого раздела)."""
        last_exc = None
        for attempt in range(3):
            await self._throttle()
            try:
                r = await self._http.get(url)
            except httpx.HTTPError as e:
                last_exc = e
                log.warning("encar %s: %s (попытка %d)", url, e.__class__.__name__, attempt + 1)
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (404, 410):
                if required:
                    raise CarNotFound(url)
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                last_exc = EncarError(f"HTTP {r.status_code}")
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                if required:
                    raise EncarError(f"HTTP {r.status_code} на {url}")
                return None
            if not r.content.strip():
                if required:
                    raise EncarError("пустой ответ Encar")
                return None
            try:
                return r.json()
            except ValueError:
                if required:
                    raise EncarError("Encar вернул не JSON (возможно, страница проверки)")
                return None
        if required:
            raise EncarError(f"Encar не отвечает: {last_exc}")
        return None

    async def vehicle(self, car_id: str) -> dict:
        data = await self._get_json(f"{API}/v1/readside/vehicle/{car_id}?include={VEHICLE_INCLUDE}",
                                    required=True)
        if not isinstance(data, dict) or not data.get("category"):
            raise CarNotFound(car_id)
        return data

    async def record(self, vehicle_id, vehicle_no: str | None):
        if not vehicle_no:
            return None
        return await self._get_json(
            f"{API}/v1/readside/record/vehicle/{vehicle_id}/open?vehicleNo={quote(str(vehicle_no))}")

    async def inspection(self, vehicle_id):
        return await self._get_json(f"{API}/v1/readside/inspection/vehicle/{vehicle_id}")

    async def diagnosis(self, vehicle_id):
        return await self._get_json(f"{API}/v1/readside/diagnosis/vehicle/{vehicle_id}")

    async def fetch_all(self, car_id: str) -> dict:
        """Карточка + все разделы истории одним вызовом."""
        vehicle = await self.vehicle(car_id)
        vid = vehicle.get("vehicleId") or car_id
        vehicle_no = vehicle.get("vehicleNo") or (vehicle.get("manage") or {}).get("vehicleNo")
        record_view = ((vehicle.get("condition") or {}).get("accident") or {}).get("recordView")
        results = await asyncio.gather(
            self.record(vid, vehicle_no) if record_view is not False else _none(),
            self.inspection(vid),
            self.diagnosis(vid),
            return_exceptions=True,
        )
        record, inspection, diagnosis = [None if isinstance(x, Exception) else x for x in results]
        for name, x in zip(("record", "inspection", "diagnosis"), results):
            if isinstance(x, Exception):
                log.warning("encar %s %s: %s", name, car_id, x)
        return {"car_id": str(car_id), "vehicle": vehicle, "record": record,
                "record_hidden": record_view is False, "inspection": inspection,
                "diagnosis": diagnosis}

    async def download_photo(self, path: str) -> bytes | None:
        for url in (photo_url(path), photo_fallback_url(path)):
            try:
                r = await self._http.get(url)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                    return r.content
            except httpx.HTTPError:
                continue
        return None

    async def ping(self) -> str:
        """Проверка доступности Encar с этого сервера (для /check)."""
        lines = []
        tests = [
            ("поиск", f"{API}/search/car/list/general?count=true&q=(And.Hidden.N.)&sr=%7CModifiedDate%7C0%7C1"),
            ("сайт", "https://fem.encar.com/"),
        ]
        for name, url in tests:
            t = time.monotonic()
            try:
                r = await self._http.get(url)
                lines.append(f"{name}: HTTP {r.status_code}, {len(r.content)} байт, "
                             f"{(time.monotonic() - t):.1f} с")
            except httpx.HTTPError as e:
                lines.append(f"{name}: ОШИБКА {e.__class__.__name__} за {(time.monotonic() - t):.1f} с")
        return "\n".join(lines)


async def _none():
    return None
