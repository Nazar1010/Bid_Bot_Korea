"""Сырые ответы Encar → аккуратная структура отчёта на русском."""
import re
from dataclasses import dataclass, field
from datetime import date

from . import dictionaries as D
from .client import DETAIL_PAGE


@dataclass
class Accident:
    date: str
    benefit: int | None          # выплата страховой, ₩
    parts: int | None
    labor: int | None
    paint: int | None

    @property
    def repair_total(self) -> int:
        return (self.parts or 0) + (self.labor or 0) + (self.paint or 0)


@dataclass
class History:
    available: bool = False
    hidden_by_seller: bool = False
    first_registration: str | None = None
    total_cases: int = 0
    own_cnt: int = 0
    own_cost: int = 0
    other_cnt: int = 0
    other_cost: int = 0
    accidents: list[Accident] = field(default_factory=list)
    total_loss: int = 0
    total_loss_date: str | None = None
    flood_total: int = 0
    flood_part: int = 0
    flood_date: str | None = None
    theft: int = 0
    owner_changes: int | None = None
    owner_change_dates: list[str] = field(default_factory=list)
    plate_changes: int = 0
    rent: bool = False
    business: bool = False
    government: bool = False
    no_insurance_periods: list[str] = field(default_factory=list)


@dataclass
class PanelIssue:
    part: str
    status: str
    frame: bool
    rank: str


@dataclass
class Inspection:
    available: bool = False
    accident: bool | None = None
    simple_repair: bool = False
    flood: bool = False
    date: str | None = None
    panels: list[PanelIssue] = field(default_factory=list)
    unit_problems: list[str] = field(default_factory=list)
    by_dealer: bool = False


@dataclass
class Diagnosis:
    available: bool = False
    no_accident_verdict: bool = False
    issues: list[str] = field(default_factory=list)


@dataclass
class CarReport:
    car_id: str
    url: str
    make: str
    model: str
    grade: str
    title: str
    year_month: str           # "05.2022"
    prod_year: int | None
    prod_month: int | None
    model_year: str
    mileage: int
    displacement: int | None
    fuel: str
    fuel_raw: str
    transmission: str
    color: str
    body: str
    seats: int | None
    price_krw: int            # цена в Корее, ₩
    new_price_krw: int | None
    vin: str | None
    seller: str
    region: str
    lease_or_rent_ad: bool
    photos: list[str]
    options: list[str]
    views: int | None
    listed_at: str | None
    warranty: str | None
    history: History
    inspection: Inspection
    diagnosis: Diagnosis
    is_electric: bool
    is_hybrid: bool


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _date(v) -> str | None:
    if not v:
        return None
    s = str(v)[:10].replace("-", "").replace(".", "")
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[6:8]}.{s[4:6]}.{s[:4]}"
    if len(s) >= 6 and s[:6].isdigit():
        return f"{s[4:6]}.{s[:4]}"
    return str(v)[:10]


def _period(v) -> str:
    """'202001~202105' или '2020-01-01~2021-05-31' → '01.2020 – 05.2021'."""
    s = str(v)
    if "~" in s:
        a, b = s.split("~", 1)
        return f"{_date(a.strip()) or a} – {_date(b.strip()) or b}"
    return _date(s) or s


def _pick_name(cat, key):
    en = cat.get(f"{key}EnglishName")
    ko = cat.get(f"{key}Name")
    if en and not D.has_hangul(en):
        return str(en).strip()
    return str(ko or "").strip()


def parse_history(rec: dict | None, hidden: bool) -> History:
    h = History(hidden_by_seller=hidden)
    if not rec or rec.get("openData") is False:
        h.hidden_by_seller = hidden or bool(rec and rec.get("openData") is False)
        return h
    h.available = True
    h.first_registration = _date(rec.get("firstDate"))
    h.own_cnt = _int(rec.get("myAccidentCnt"))
    h.own_cost = _int(rec.get("myAccidentCost"))
    h.other_cnt = _int(rec.get("otherAccidentCnt"))
    h.other_cost = _int(rec.get("otherAccidentCost"))
    h.total_cases = _int(rec.get("accidentCnt")) or h.own_cnt + h.other_cnt
    for a in rec.get("accidents") or []:
        if not isinstance(a, dict) or not a.get("date"):
            continue
        h.accidents.append(Accident(
            date=_date(a.get("date")) or "",
            benefit=_int(a.get("insuranceBenefit"), None),
            parts=_int(a.get("partCost"), None),
            labor=_int(a.get("laborCost"), None),
            paint=_int(a.get("paintingCost"), None),
        ))
    h.total_loss = _int(rec.get("totalLossCnt"))
    h.total_loss_date = _date(rec.get("totalLossDate"))
    h.flood_total = _int(rec.get("floodTotalLossCnt"))
    h.flood_part = _int(rec.get("floodPartLossCnt"))
    h.flood_date = _date(rec.get("floodDate"))
    h.theft = _int(rec.get("robberCnt"))
    if isinstance(rec.get("ownerChangeCnt"), int):
        h.owner_changes = rec["ownerChangeCnt"]
    h.owner_change_dates = [_date(d) for d in rec.get("ownerChanges") or [] if isinstance(d, str)]
    h.plate_changes = _int(rec.get("carNoChangeCnt"))
    h.rent = _int(rec.get("loan")) > 0
    h.business = _int(rec.get("business")) > 0
    h.government = _int(rec.get("government")) > 0
    h.no_insurance_periods = [_period(rec[f"notJoinDate{i}"]) for i in range(1, 6)
                              if rec.get(f"notJoinDate{i}")]
    return h


def parse_inspection(data: dict | None) -> Inspection:
    ins = Inspection()
    if not data:
        return ins
    ins.available = True
    master = data.get("master") or {}
    detail = master.get("detail") or {}
    if isinstance(master.get("accdient"), bool):          # опечатка в самом API Encar
        ins.accident = master["accdient"]
    ins.simple_repair = bool(master.get("simpleRepair"))
    ins.flood = detail.get("waterlog") is True
    ins.date = _date(master.get("registrationDate"))
    src = data.get("inspectionSource") or {}
    ins.by_dealer = src.get("code") == "DIRECT_INPUT"
    for o in data.get("outers") or []:
        if not isinstance(o, dict):
            continue
        pairs = [(s.get("code"), s.get("title")) for s in o.get("statusTypes") or []
                 if isinstance(s, dict)]
        if o.get("status"):
            pairs.append((o["status"], None))
        statuses = []
        for c, t in pairs:
            ru = D.OUTER_STATUS.get(str(c)) if c else None
            if not ru and t:
                ru = next((v for k, v in D.OUTER_STATUS_BY_TITLE.items() if k in t), t)
            if ru and ru not in statuses:
                statuses.append(ru)
        if not statuses:
            continue
        attrs = o.get("attributes") or []
        rank = next((a for a in ("RANK_B", "RANK_A", "RANK_TWO", "RANK_ONE") if a in attrs), "")
        ins.panels.append(PanelIssue(
            part=D.translate_part((o.get("type") or {}).get("title")),
            status=", ".join(statuses),
            frame=rank in D.FRAME_RANKS,
            rank=D.OUTER_RANKS.get(rank, ""),
        ))
    ins.panels.sort(key=lambda p: (not p.frame, p.part))

    def walk(node, group, parent=""):
        st = node.get("statusType") or {}
        code = str(st.get("code") or "")
        title = D.translate_inner((node.get("type") or {}).get("title"))
        children = [c for c in node.get("children") or [] if isinstance(c, dict)]
        if not code:
            for c in children:
                walk(c, group, " · ".join(x for x in (parent, title) if x))
            return
        level, label = D.INNER_STATUS.get(code, ("warn", st.get("title") or "отклонение"))
        if level != "ok":
            name = " · ".join(x for x in (parent, title) if x)
            ins.unit_problems.append(f"{group}: {name} — {label}" if name else f"{group}: {label}")

    for g in data.get("inners") or []:
        if not isinstance(g, dict):
            continue
        gcode = str((g.get("type") or {}).get("code") or "")
        if gcode == "S00":
            continue
        gname = D.INNER_GROUPS.get(gcode, D.translate_inner((g.get("type") or {}).get("title")) or "узел")
        for c in g.get("children") or []:
            if isinstance(c, dict):
                walk(c, gname)
    return ins


def parse_diagnosis(data: dict | None) -> Diagnosis:
    d = Diagnosis()
    items = (data or {}).get("items") or []
    if not items:
        return d
    d.available = True
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "")
        if name in ("CHECKER_COMMENT", "OUTER_PANEL_COMMENT"):
            if "무사고" in str(it.get("result") or ""):
                d.no_accident_verdict = True
            continue
        code = str(it.get("resultCode") or "").upper()
        if not code or code == "NORMAL":
            continue
        d.issues.append(f"{D.DIAG_ZONES.get(name, name.lower())} — {D.DIAG_RESULTS.get(code, code.lower())}")
    return d


def build_report(raw: dict) -> CarReport:
    v = raw["vehicle"]
    cat = v.get("category") or {}
    spec = v.get("spec") or {}
    adv = v.get("advertisement") or {}
    manage = v.get("manage") or {}
    contact = v.get("contact") or {}

    make = _pick_name(cat, "manufacturer")
    make = D.lookup(D.MAKES, make, make) if D.has_hangul(make) else make
    model_group = _pick_name(cat, "modelGroup")
    model_full = str(cat.get("modelName") or "").strip()
    grade = " ".join(x for x in (_pick_name(cat, "grade"), _pick_name(cat, "gradeDetail"))
                     if x and "없음" not in x)
    # Название модели: английская группа + поколение в скобках из корейского названия
    gen = ""
    if "(" in model_full:
        gen = model_full[model_full.find("("):].strip()
    else:
        tags = []
        m = re.search(r"(\d+)\s*세대", model_full)
        if m:
            tags.append(f"{m.group(1)}-е поколение")
        if "더 뉴" in model_full or "더뉴" in model_full:
            tags.append("рестайлинг")
        if tags:
            gen = "(" + ", ".join(tags) + ")"
    model = (model_group + (" " + gen if gen and gen not in model_group else "")).strip()

    ym = str(cat.get("yearMonth") or "")
    prod_year = _int(ym[:4], None) if len(ym) >= 4 else None
    prod_month = _int(ym[4:6], None) if len(ym) >= 6 else None

    fuel_raw = str(spec.get("fuelName") or "")
    photos = []
    for p in v.get("photos") or []:
        if isinstance(p, dict) and p.get("path") and str(p.get("type") or "").upper() not in (
                "DIAG", "DIAG2", "THUMBNAIL"):
            if p["path"] not in photos:
                photos.append(p["path"])
    photos.sort()                       # _001, _002 … — порядок, как на сайте

    options = []
    opts = v.get("options") or {}
    for code in opts.get("standard") or []:
        if str(code) in D.OPTIONS:
            options.append(D.OPTIONS[str(code)])

    sell_type = str(adv.get("type") or "")
    lease = bool(adv.get("leaseRentInfo")) or sell_type in ("LEASE", "RENT")

    warranty = None
    w = cat.get("warranty") or {}
    end_y, end_m = None, None
    if w.get("bodyMonth") and len(ym) >= 6:
        total = _int(ym[:4]) * 12 + _int(ym[4:6]) - 1 + _int(w["bodyMonth"])
        end_y, end_m = divmod(total, 12)
        end_m += 1
    still_valid = end_y is not None and (end_y, end_m) >= (date.today().year, date.today().month)
    if still_valid and _int(spec.get("mileage")) < _int(w.get("bodyMileage"), 10**9):
        company = D.lookup(D.MAKES, w.get("companyName"), None) or "заводская"
        warranty = (f"{company}: кузов/двигатель "
                    f"{w.get('bodyMonth') or '?'} мес. / {_int(w.get('bodyMileage')):,} км, до {end_m:02d}.{end_y}").replace(",", " ")

    vin = v.get("vin")
    if not (isinstance(vin, str) and len(vin) == 17 and vin.isalnum()):
        vin = None

    return CarReport(
        car_id=raw["car_id"],
        url=DETAIL_PAGE.format(raw["car_id"]),
        make=make,
        model=model,
        grade=grade,
        title=f"{make} {model}".strip(),
        year_month=f"{prod_month:02d}.{prod_year}" if prod_year and prod_month else (str(prod_year or "")),
        prod_year=prod_year,
        prod_month=prod_month,
        model_year=str(cat.get("formYear") or prod_year or ""),
        mileage=_int(spec.get("mileage")),
        displacement=_int(spec.get("displacement"), None) or None,
        fuel=D.lookup(D.FUEL, fuel_raw, fuel_raw),
        fuel_raw=fuel_raw,
        transmission=D.lookup(D.TRANSMISSION, spec.get("transmissionName"), ""),
        color=D.lookup(D.COLORS, spec.get("colorName") or spec.get("customColor"), ""),
        body=D.lookup(D.BODY, spec.get("bodyName"), ""),
        seats=_int(spec.get("seatCount"), None) or None,
        price_krw=_int(adv.get("price")) * 10_000,                 # в API — в манах (10 000 ₩)
        new_price_krw=(_int(cat.get("originPrice")) * 10_000) or None,
        vin=vin,
        seller=D.SELLER_TYPES.get(str(contact.get("userType") or "").upper(), "дилер"),
        region=D.region(contact.get("address")),
        lease_or_rent_ad=lease,
        photos=photos,
        options=options,
        views=_int(manage.get("viewCount"), None),
        listed_at=_date(manage.get("firstAdvertisedDateTime") or manage.get("registDateTime")),
        warranty=warranty,
        history=parse_history(raw.get("record"), raw.get("record_hidden", False)),
        inspection=parse_inspection(raw.get("inspection")),
        diagnosis=parse_diagnosis(raw.get("diagnosis")),
        is_electric=fuel_raw.strip() == "전기",
        is_hybrid="+전기" in fuel_raw or "하이브리드" in fuel_raw,
    )


def age_years(rep: CarReport, on: date) -> float | None:
    """Возраст авто (лет) на дату — от месяца выпуска."""
    if not rep.prod_year:
        return None
    m = rep.prod_month or 1
    return (on.year - rep.prod_year) + (on.month - m) / 12 + (on.day - 1) / 365
