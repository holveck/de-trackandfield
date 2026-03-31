# streamlit_app.py
# ---------------------------------------------------------
# Delaware Online Sports Compendium

import io
import re
import math
import difflib
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import pandas as pd
import streamlit as st
import openpyxl
import altair as alt

# ----------------------------
# Canonical dictionaries & aliases
# ----------------------------

MEETS: Dict[str, set] = {
    "Division I": {"division 1", "div i", "d1"},
    "Division II": {"division 2", "div ii", "d2"},
    "Meet of Champions": {"moc", "meet of champs", "meet of champion"},
    "New Castle County": {"ncc", "new castle", "new castle co", "new castle county", "new castle county championships"},
    "Henlopen Conference": {"henlopen"},
    "Indoor State Championship": {
        "indoor", "indoor state", "state indoor", "indoor championship",
        "indoor state championship", "indoor state championships", "indoor state meet", "indoor state meets"
    },
}
MEET_CANONICAL: Dict[str, str] = {}
for canonical, synonyms in MEETS.items():
    MEET_CANONICAL[canonical.lower()] = canonical
    for s in synonyms:
        MEET_CANONICAL[s.lower()] = canonical

EVENTS: Dict[str, set] = {
    "100/55": {"55", "55m", "100", "100m"},
    "200": {"200m"},
    "400": {"400m"},
    "800": {"800m"},
    "1600": {"1600m", "mile"},
    "3200": {"3200m", "2mile", "two mile", "2-mile", "two-mile"},
    "100/55H": {"55h", "55 hurdles", "100h", "100 hurdles", "girls hurdles"},
    "110/55H": {"110h", "110 hurdles", "boys hurdles"},
    "300H": {"300h", "300 hurdles"},
    "4x100": {"4x1", "4 x 100"},
    "4x200": {"4x2", "4 x 200"},
    "4x400": {"4x4", "4 x 400"},
    "4x800": {"4x8", "4 x 800"},
    "HJ": {"high jump", "h/j"},
    "PV": {"pole vault", "p/v"},
    "LJ": {"long jump", "l/j"},
    "TJ": {"triple jump", "t/j"},
    "Shot put": {"shot", "shotput", "sp"},
    "Discus": {"disc", "discus throw"},
}
EVENT_CANONICAL: Dict[str, str] = {}
for canonical, synonyms in EVENTS.items():
    EVENT_CANONICAL[canonical.lower()] = canonical
    for s in synonyms:
        EVENT_CANONICAL[s.lower()] = canonical

GENDER_ALIASES: Dict[str, set] = {
    "girls": {"girls", "girl", "g", "women", "female"},
    "boys": {"boys", "boy", "b", "men", "male"},
}
GENDER_CANONICAL: Dict[str, str] = {}
for canonical, synonyms in GENDER_ALIASES.items():
    GENDER_CANONICAL[canonical] = canonical.upper()
    for s in synonyms:
        GENDER_CANONICAL[s] = canonical.upper()

# ---------- Event groups ----------
EVENT_GROUPS = {
    "distance": {"800", "1600", "3200"},
    "sprints": {"100/55", "110/55H", "200", "400", "300H"},
    "hurdles": {"110/55H", "300H"},
    "field": {"HJ", "LJ", "TJ", "Shot put", "Discus", "PV"},
    "jumps": {"HJ", "LJ", "TJ"},
    "throws": {"Shot put", "Discus"},
    "relays": {"4x100", "4x200", "4x400", "4x800"},
}
EVENT_GROUP_SYNONYMS = {
    "sprint": "sprints", "sprinters": "sprints", "sprints": "sprints",
    "distance": "distance", "distances": "distance", "distance events": "distance",
    "hurdle": "hurdles", "hurdles": "hurdles", "hurdling": "hurdles",
    "field": "field", "field events": "field",
    "jump": "jumps", "jumps": "jumps", "field jumps": "jumps",
    "throw": "throws", "throws": "throws",
    "relay": "relays", "relays": "relays", "4x": "relays",
}
TRACK_EVENTS = {"100/55","200","400","800","1600","3200","100/55H","110/55H","300H","4x100","4x200","4x400","4x800"}
FIELD_EVENTS = {"LJ","TJ","HJ","PV","Shot put","Discus"}

# ----------------------------
# State-meet definitions
# ----------------------------
STATE_MEETS_OUTDOOR = {"Division I", "Division II"}
STATE_MEETS_INDOOR = {"Indoor State Championship"}
STATE_MEETS_ALL = STATE_MEETS_OUTDOOR | STATE_MEETS_INDOOR

# ----------------------------
# Champions parsing
# ----------------------------

def detect_year_bundles(ws) -> List[Tuple[int, int]]:
    bundles: List[Tuple[int, int]] = []
    col = 1
    maxc = ws.max_column
    while col <= maxc:
        v = ws.cell(row=1, column=col).value
        if isinstance(v, (int, float)) and 1900 < float(v) < 2100:
            bundles.append((int(v), col))
            col += 4
        else:
            col += 1
    return bundles


def normalize_event_label(raw) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        s = str(int(raw)) if float(raw).is_integer() else str(raw)
        return s.replace(".0", "")
    s = str(raw).strip()
    low = s.lower()
    if low in EVENT_CANONICAL:
        return EVENT_CANONICAL[low]
    if low.endswith(".0") and low[:-2] in EVENT_CANONICAL:
        return EVENT_CANONICAL[low[:-2]]
    if re.fullmatch(r"\d{2,4}", s):
        return s
    return s


def canonical_meet(token: str) -> Optional[str]:
    low = token.strip().lower()
    if low in MEET_CANONICAL:
        return MEET_CANONICAL[low]
    choice = difflib.get_close_matches(low, list(MEET_CANONICAL.keys()), n=1, cutoff=0.85)
    if choice:
        return MEET_CANONICAL[choice[0]]
    return None


def parse_champions_sheet(ws, gender: str) -> pd.DataFrame:
    year_bundles = detect_year_bundles(ws)
    records = []
    current_event: Optional[str] = None

    for r in range(1, ws.max_row + 1):
        ev_raw = ws.cell(row=r, column=1).value
        if ev_raw:
            maybe_ev = normalize_event_label(ev_raw)
            if maybe_ev:
                current_event = maybe_ev

        meet_raw = ws.cell(row=r, column=5).value
        if current_event and isinstance(meet_raw, str):
            meet_name = canonical_meet(meet_raw)
            if not meet_name:
                continue
            for (year, c0) in year_bundles:
                name = ws.cell(row=r, column=c0).value
                clas = ws.cell(row=r, column=c0 + 1).value
                school = ws.cell(row=r, column=c0 + 2).value
                mark = ws.cell(row=r, column=c0 + 3).value
                if name:
                    records.append(
                        {
                            "gender": gender,
                            "event": current_event,
                            "meet": meet_name,
                            "year": int(year),
                            "name": str(name).strip() if name else None,
                            "class": str(clas).strip() if clas else None,
                            "school": str(school).strip() if school else None,
                            "mark": mark if mark is not None else None,
                        }
                    )
    return pd.DataFrame.from_records(records)


@st.cache_data(show_spinner=False)
def load_champions(file_bytes: bytes) -> pd.DataFrame:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    girls_df = parse_champions_sheet(wb["GIRLS"], "GIRLS")
    boys_df = parse_champions_sheet(wb["BOYS"], "BOYS")
    out = pd.concat([girls_df, boys_df], ignore_index=True)
    out["event"] = out["event"].astype(str).str.replace(r"\.0$", "", regex=True)
    out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")
    return out

# ----------------------------
# MVPs parsing
# ----------------------------
MVP_CATEGORY_MAP = {
    "Girls Indoor Track and Field": ("GIRLS", "Indoor"),
    "Boys Indoor Track and Field": ("BOYS", "Indoor"),
    "Girls Outdoor Track and Field": ("GIRLS", "Outdoor"),
    "Boys Outdoor Track and Field": ("BOYS", "Outdoor"),
    "Girls Cross Country": ("GIRLS", "Cross Country"),
    "Boys Cross Country": ("BOYS", "Cross Country"),
}


def _find_header_row(ws) -> Optional[int]:
    maxr = ws.max_row
    for r in range(1, min(maxr, 50) + 1):
        c1 = ws.cell(row=r, column=1).value
        if isinstance(c1, str) and c1.strip().lower() == "year":
            headers = [ws.cell(row=r, column=c).value for c in range(2, ws.max_column + 1)]
            header_set = {str(h).strip() for h in headers if h}
            if header_set & set(MVP_CATEGORY_MAP.keys()):
                return r
    return None


def _parse_season_label(lbl) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    if lbl is None:
        return None, None, None
    try:
        import datetime as _dt
        if isinstance(lbl, (_dt.datetime, _dt.date)):
            y = int(lbl.year)
            return y, y, str(y)
    except Exception:
        pass
    s = str(lbl).strip()
    m = re.match(r"^(20\d{2})\s*[-/]\s*(\d{2}|\d{4})$", s)
    if m:
        y1 = int(m.group(1))
        y2txt = m.group(2)
        y2 = int(y2txt) if len(y2txt) == 4 else int(str(y1)[:2] + y2txt)
        return y1, y2, f"{y1}-{str(y2)[-2:]}"
    m2 = re.match(r"^(20\d{2})$", s)
    if m2:
        y = int(m2.group(1))
        return y, y, s
    return None, None, None


@st.cache_data(show_spinner=False)
def load_mvps(file_bytes: bytes) -> pd.DataFrame:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    if "MVPs" not in wb.sheetnames:
        return pd.DataFrame(columns=[
            "season_label","season_start","season_end","category","gender","scope","name","school"
        ])
    ws = wb["MVPs"]
    header_row = _find_header_row(ws)
    if not header_row:
        return pd.DataFrame(columns=[
            "season_label","season_start","season_end","category","gender","scope","name","school"
        ])

    year_col = None
    cat_cols: Dict[str, Tuple[int, int]] = {}
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=header_row, column=c).value
        if not v:
            continue
        label = str(v).strip()
        if label.lower() == "year":
            year_col = c
        elif label in MVP_CATEGORY_MAP:
            cat_cols[label] = (c, c + 1)

    if year_col is None or not cat_cols:
        return pd.DataFrame(columns=[
            "season_label","season_start","season_end","category","gender","scope","name","school"
        ])

    start_row = header_row + 1
    current = {"season_label": None, "season_start": None, "season_end": None}
    records = []

    for r in range(start_row, ws.max_row + 1):
        ycell = ws.cell(row=r, column=year_col).value
        if ycell not in (None, ""):
            y1, y2, lbl = _parse_season_label(ycell)
            if lbl:
                current = {"season_label": lbl, "season_start": y1, "season_end": y2}
        if not current["season_label"]:
            continue
        for cat, (name_col, school_col) in cat_cols.items():
            name = ws.cell(row=r, column=name_col).value
            if name not in (None, ""):
                school = ws.cell(row=r, column=school_col).value if school_col <= ws.max_column else None
                gender, scope = MVP_CATEGORY_MAP[cat]
                records.append({
                    "season_label": current["season_label"],
                    "season_start": current["season_start"],
                    "season_end": current["season_end"],
                    "category": cat,
                    "gender": gender,
                    "scope": scope,
                    "name": str(name).strip(),
                    "school": str(school).strip() if school else None,
                })
    return pd.DataFrame.from_records(records).drop_duplicates().reset_index(drop=True)

# ----------------------------
# All-time lists: mark parsing & sort prefs
# ----------------------------
LOWER_BETTER = {
    "100/55","200","400","800","1600","3200",
    "100/55H","110/55H","300H",
    "4x100","4x200","4x400","4x800"
}
HIGHER_BETTER = {"LJ","TJ","HJ","PV","Shot put","Discus"}


def _parse_time_to_seconds(txt: str) -> Optional[float]:
    if txt is None:
        return None
    s = str(txt).strip()
    s = re.sub(r"[a-zA-Z]+$", "", s).strip()
    try:
        if ":" not in s:
            return float(s)
        parts = [p.strip() for p in s.split(":")]
        if len(parts) == 2:
            m, sec = parts
            return 60.0*float(m) + float(sec)
        if len(parts) == 3:
            h, m, sec = parts
            return 3600.0*float(h) + 60.0*float(m) + float(sec)
    except Exception:
        return None
    return None


def _parse_distance_to_float(txt: str) -> Optional[float]:
    if txt is None:
        return None
    s = str(txt).strip().lower()
    s = re.sub(r"[a-z]+$", "", s).strip()
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$", s)
    if m:
        feet = float(m.group(1))
        inches = float(m.group(2))
        return feet*12.0 + inches
    try:
        return float(s)
    except Exception:
        return None


def _mark_sort_key(ev: str, mark: str):
    if ev in LOWER_BETTER:
        secs = _parse_time_to_seconds(mark)
        return (0, secs if secs is not None else math.inf)
    if ev in HIGHER_BETTER:
        val = _parse_distance_to_float(mark)
        return (0, -val if val is not None else math.inf)
    return (1, math.inf)

# ----------------------------
# Loaders for All-Time (per-event) and State Records (first sheet)
# ----------------------------
@st.cache_data(show_spinner=False)

def load_alltime(file_bytes: bytes, file_type: str = "xlsx") -> pd.DataFrame:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    rows_all = []

    def find_header_row(ws) -> Optional[int]:
        max_probe = min(ws.max_row, 25)
        for r in range(1, max_probe+1):
            lower = []
            for c in range(1, ws.max_column+1):
                v = ws.cell(row=r, column=c).value
                lower.append(str(v).strip().lower() if v is not None else "")
            if ("name" in lower) and (("time" in lower) or ("mark" in lower) or ("time/mark" in lower)):
                return r
        return None

    for title in wb.sheetnames:
        ws = wb[title]
        sheet_title = title.strip()
        m = re.match(r"^(Girls|Boys)\s+(.+)$", sheet_title, flags=re.IGNORECASE)
        if not m:
            continue
        gender_txt = m.group(1).upper()
        gender = "GIRLS" if gender_txt.startswith("GIRL") else "BOYS"
        event_raw = m.group(2).strip()
        event_norm_guess = re.sub(r"[,\s]+", " ", event_raw).strip()
        event_norm_guess = event_norm_guess.replace(",", "").replace("1,600","1600").replace("3,200","3200")
        event_canonical = canonical_event(event_norm_guess, gender=None) or event_norm_guess

        hdr_row = find_header_row(ws)
        if not hdr_row:
            continue

        headers = []
        for c in range(1, ws.max_column+1):
            v = ws.cell(row=hdr_row, column=c).value
            headers.append(str(v).strip().lower() if v is not None else "")
        data = []
        for r in range(hdr_row+1, ws.max_row+1):
            row_vals = [ws.cell(row=r, column=c).value for c in range(1, ws.max_column+1)]
            if all(v in (None, "", " ") for v in row_vals):
                continue
            data.append(row_vals)
        df = pd.DataFrame(data, columns=headers)

        perf_col = None
        for cand in ["time/mark","time","mark"]:
            if cand in df.columns:
                perf_col = cand
                break
        if perf_col is None:
            perf_col = next((c for c in df.columns if c.replace(" ", "") in {"time","mark","time/mark"}), None)
        if perf_col is None:
            continue

        rename_map = {}
        for c in df.columns:
            lc = c.lower().strip()
            if lc in {"time/mark","time","mark"}:
                rename_map[c] = "mark"
            elif lc in {"name","athlete"}:
                rename_map[c] = "name"
            elif lc == "school":
                rename_map[c] = "school"
            elif lc == "meet":
                rename_map[c] = "meet"
            elif lc == "date":
                rename_map[c] = "date"
            elif lc == "location":
                rename_map[c] = "location"
            elif lc in {"wind reading","wind"}:
                rename_map[c] = "wind"
            elif lc == "athletes":
                rename_map[c] = "athletes"
            elif lc in {"rank", "#", "no", "number"}:
                rename_map[c] = "rank"
            else:
                rename_map[c] = c
        df = df.rename(columns=rename_map)

        df = df[(df["mark"].notna()) & ((df.get("name").notna()) | (df.get("school").notna()))].copy()

        if "date" in df.columns:
            def _to_year(val):
                try:
                    if isinstance(val, (int, float)):
                        dt = pd.to_datetime(val, unit="d", origin="1899-12-30", errors="coerce")
                        return int(dt.year) if pd.notna(dt) else None
                    dt = pd.to_datetime(str(val), errors="coerce")
                    return int(dt.year) if pd.notna(dt) else None
                except Exception:
                    return None
            df["year"] = df["date"].apply(_to_year)
        else:
            df["year"] = None

        if "name" not in df.columns:
            df["name"] = None
        df.loc[df["name"].isna() & df.get("school").notna(), "name"] = df.loc[df["name"].isna() & df.get("school").notna(), "school"]

        df["gender"] = gender
        df["event"]  = event_canonical

        df["source_name"] = "all_time"
        df["ingested_at"] = pd.Timestamp.utcnow().isoformat()
        df["__sortkey"] = df.apply(lambda r: _mark_sort_key(str(df.at[r.name,"event"]), str(df.at[r.name,"mark"])), axis=1)

        base_cols = ["rank","gender","event","mark","name","school","year","meet","location","wind","athletes","source_name","ingested_at","__sortkey"]
        keep = [c for c in base_cols if c in df.columns]
        rows_all.append(df[keep])

    if not rows_all:
        return pd.DataFrame(columns=["gender","event","mark","name","school","year","meet"])

    out = pd.concat(rows_all, ignore_index=True)
    out["event"] = out["event"].apply(lambda e: canonical_event(str(e), None) or str(e))
    out = out[~out["name"].astype(str).str.lower().str.contains(r"^wind\s*reading$", regex=True, na=False)]
    return out.reset_index(drop=True)

# ---- New: event normalization helper for state-records ----

def normalize_event_text_for_records(raw) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        f = float(raw)
        if math.isfinite(f) and f.is_integer():
            s = str(int(f))
        else:
            s = str(raw)
    else:
        s = str(raw).strip()

    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]

    ev = canonical_event(s, None)
    if ev:
        return ev

    maybe = normalize_event_label(s)
    if maybe:
        return canonical_event(maybe, None) or maybe

    return s


@st.cache_data(show_spinner=False)
def load_state_records_first_sheet(file_bytes: bytes) -> pd.DataFrame:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    if not wb.worksheets:
        return pd.DataFrame(columns=["gender","event","mark","name","school","athletes","meet","location","year"])
    ws = wb.worksheets[0]

    def to_year(val):
        try:
            if isinstance(val, (int, float)):
                dt = pd.to_datetime(val, unit="d", origin="1899-12-30", errors="coerce")
                return int(dt.year) if pd.notna(dt) else None
            dt = pd.to_datetime(str(val), errors="coerce")
            return int(dt.year) if pd.notna(dt) else None
        except Exception:
            return None

    def clean_txt(v):
        return None if v in (None, "") else str(v).strip()

    def current_event_from_cells(g_raw, h_raw, last_event):
        ev_raw = h_raw if h_raw not in (None, "") else g_raw
        ev_norm = normalize_event_text_for_records(ev_raw)
        return ev_norm or last_event

    def is_relay_event(ev_canon: str) -> bool:
        return ev_canon in EVENT_GROUPS["relays"]

    header_like = {"time","mark","time/mark","name","school","meet","location","date"}

    rows_out = []
    last_event = None
    for r in range(1, ws.max_row + 1):
        g_raw = ws.cell(row=r, column=7).value
        h_raw = ws.cell(row=r, column=8).value
        last_event = current_event_from_cells(g_raw, h_raw, last_event)
        if not last_event:
            continue

        ev_canon = normalize_event_text_for_records(last_event) or str(last_event)

        b_cols = [ws.cell(row=r, column=c).value for c in range(1, 7)]
        g_cols = [ws.cell(row=r, column=c).value for c in range(9, 15)]

        b_time = clean_txt(b_cols[0])
        g_time = clean_txt(g_cols[0])
        if (b_time and b_time.lower() in header_like) or (g_time and g_time.lower() in header_like):
            continue

        relay = is_relay_event(ev_canon)

        def add_record(side_cols, gender_label: str):
            if not side_cols:
                return
            time_mark = clean_txt(side_cols[0])
            if not time_mark:
                return
            if relay:
                school = clean_txt(side_cols[1])
                athletes = clean_txt(side_cols[2])
                name = athletes
                meet = clean_txt(side_cols[3])
                location = clean_txt(side_cols[4])
                date = side_cols[5]
            else:
                name = clean_txt(side_cols[1])
                school = clean_txt(side_cols[2])
                meet = clean_txt(side_cols[3])
                location = clean_txt(side_cols[4])
                date = side_cols[5]
                athletes = None
            rows_out.append({
                "gender": gender_label,
                "event": ev_canon,
                "mark": time_mark,
                "name": name,
                "school": school,
                "athletes": athletes,
                "meet": meet,
                "location": location,
                "year": to_year(date),
                "source_name": "state_records",
                "ingested_at": pd.Timestamp.utcnow().isoformat(),
            })

        add_record(b_cols, "BOYS")
        add_record(g_cols, "GIRLS")

    if not rows_out:
        return pd.DataFrame(columns=["gender","event","mark","name","school","athletes","meet","location","year"])

    df = pd.DataFrame.from_records(rows_out)
    df["event"] = df["event"].apply(lambda e: canonical_event(str(e), None) or str(e))
    df["event"] = df["event"].astype(str).str.replace(r"\.0$", "", regex=True)

    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")

    cols = ["gender","event","mark","name","school","athletes","meet","location","year","source_name","ingested_at"]
    df = df[cols]
    return df.reset_index(drop=True)

# ----------------------------
# Athlete/title utilities
# ----------------------------

def normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower()) if isinstance(s, str) else ""


@st.cache_data(show_spinner=False)
def all_athletes_index(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    tmp["name_norm"] = tmp["name"].apply(normalize_name)
    schools = (
        tmp.groupby(["gender", "name", "name_norm"])['school']
        .apply(lambda x: ", ".join(sorted({s for s in x.dropna()})))
        .reset_index(name="schools")
    )
    return schools.sort_values(["gender", "name"]).reset_index(drop=True)


def title_count(df: pd.DataFrame, athlete_name: str, *, include_meets: set, include_relays: bool = False):
    nn = normalize_name(athlete_name)
    cur = df.copy()
    cur["name_norm"] = cur["name"].apply(normalize_name)
    cur = cur[cur["name_norm"] == nn]
    cur = cur[cur["meet"].isin(include_meets)]
    if not include_relays:
        cur = cur[~cur["event"].isin({"4x100", "4x200", "4x400", "4x800"})]
    return len(cur), cur.sort_values(["year", "meet", "event"])


def guess_gender_for_name(df: pd.DataFrame, athlete_name: str) -> List[str]:
    nn = normalize_name(athlete_name)
    g = df.assign(name_norm=df["name"].apply(normalize_name))
    found = g[g["name_norm"] == nn]["gender"].dropna().unique().tolist()
    return found or ["GIRLS", "BOYS"]

# ----------------------------
# Table + Rendering helpers
# ----------------------------

def _format_gender_values(df: pd.DataFrame) -> pd.DataFrame:
    if "gender" in df.columns:
        df = df.copy()
        df["gender"] = df["gender"].map({"GIRLS": "Girls", "BOYS": "Boys"}).fillna(df["gender"])
    return df


def _reorder_gender_first(df: pd.DataFrame) -> pd.DataFrame:
    if "gender" in df.columns:
        cols = ["gender"] + [c for c in df.columns if c != "gender"]
        return df[cols]
    return df


def _pretty_headers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c[:1].upper() + c[1:] if c else c for c in df.columns]
    return df


def show_table(df: pd.DataFrame, cols: Optional[List[str]] = None):
    if df is None:
        return
    cur = df.copy()
    if cols:
        keep = [c for c in cols if c in cur.columns]
        cur = cur[keep]
    cur = _format_gender_values(cur)
    cur = _reorder_gender_first(cur)
    cur = _pretty_headers(cur)
    st.dataframe(cur, use_container_width=True, hide_index=True)


def metric_row(metrics: List[Tuple[str, str]]):
    cols = st.columns(len(metrics))
    for i, (label, value) in enumerate(metrics):
        cols[i].metric(label, value)

# ----------------------------
# Leaderboard (wins)
# ----------------------------

def _apply_filters_for_leaderboard(df_: pd.DataFrame, f: Dict[str, Optional[str]]) -> pd.DataFrame:
    cur = df_.copy()
    cur["year_num"] = pd.to_numeric(cur.get("year"), errors="coerce")
    if f.get("genders"): cur = cur[cur["gender"].isin(set(f["genders"]))]
    if f.get("meets"):   cur = cur[cur["meet"].isin(set(f["meets"]))]
    if f.get("events"):  cur = cur[cur["event"].isin(set(f["events"]))]
    if f.get('schools_exact'):
        cur = cur[cur['school'].isin(set(f['schools_exact']))]
    elif f.get('schools'):
        mask = pd.Series(False, index=cur.index)
        for s in f['schools']:
            needle = s.lower(); mask = mask | cur['school'].str.lower().str.contains(needle, na=False)
        cur = cur[mask]
    yf, yt = f.get("year_from"), f.get("year_to")
    if yf and yt: cur = cur[(cur["year_num"] >= yf) & (cur["year_num"] <= yt)]
    elif yf and not yt: cur = cur[cur["year_num"] >= yf]
    if f.get("track_only"):
        cur = cur[cur["event"].isin(TRACK_EVENTS)]
        cur = cur[~cur["event"].isin(EVENT_GROUPS["relays"])]
    return cur


def leaderboard_wins(df_: pd.DataFrame, f: Dict[str, Optional[str]]) -> pd.DataFrame:
    cur = _apply_filters_for_leaderboard(df_, f)
    if cur.empty:
        return pd.DataFrame(columns=["gender","name","school","wins"])
    grp_cols = ["gender","name","school"] if (f.get("genders")) else ["name","school","gender"]
    lb = (cur.groupby(grp_cols).size().reset_index(name="wins").sort_values(["wins","name"], ascending=[False, True]))
    topn = max(1, int(f.get("top_n", 10)))
    return lb.head(topn)

# ----------------------------
# NL helpers + multi-condition parsing & leaderboards
# ----------------------------

def canonical_event(event_text: str, gender: Optional[str]) -> Optional[str]:
    t = event_text.strip().lower()
    if t in EVENT_CANONICAL:
        return EVENT_CANONICAL[t]
    if "hurd" in t or t in {"55h", "110h", "100h"}:
        if gender == "GIRLS":
            return "100/55H"
        if gender == "BOYS":
            return "110/55H"
        if "55" in t:
            return "100/55H"
    if t in {"55", "55m"}:
        return "100/55"
    keys = list({v.lower(): v for v in EVENT_CANONICAL.values()}.keys())
    m = difflib.get_close_matches(t, keys, n=1, cutoff=0.8)
    return EVENT_CANONICAL[m[0]] if m else None


def _tokenize_phrases(q: str) -> List[str]:
    s = q.lower()
    for sep in [" or ", " / ", " | ", " & ", ";"]:
        s = s.replace(sep, ",")
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return parts


def _expand_event_groups(words: List[str]) -> set:
    out = set()
    for w in words:
        w = w.strip().lower()
        g = EVENT_GROUP_SYNONYMS.get(w) or (w if w in EVENT_GROUPS else None)
        if g and g in EVENT_GROUPS:
            out |= EVENT_GROUPS[g]
            continue
        ev = canonical_event(w, None)
        if ev:
            out.add(ev)
    return out


_EVENT_MULTIWORD = ["long jump", "triple jump", "high jump", "pole vault", "shot put", "discus"]

def _mask_decades(text: str) -> str:
    t = text
    t = re.sub(r"\b(?:18|19|20)\d{2}s\b", " ", t)
    t = re.sub(r"\b(?:18|19|20)\d0s\b", " ", t)
    t = re.sub(r"\b[’']?\d{2}s\b", " ", t)
    return t


def _extract_events_anywhere(q: str) -> set:
    low = q.lower().replace("’", "'")
    masked = _mask_decades(low)
    events = set()

    for phrase in _EVENT_MULTIWORD:
        if re.search(rf"\b{re.escape(phrase)}\b", masked):
            events.add(EVENT_CANONICAL[phrase])

    for code in ["300h", "110h", "100h"]:
        if re.search(rf"\b{code}\b", masked):
            ev = canonical_event(code, None)
            if ev:
                events.add(ev)

    for num in ["55", "100", "200", "400", "800", "1600", "3200"]:
        if re.search(rf"(?<!\d)\b{num}\b(?!\d|0s)", masked):
            ev = canonical_event(num, None)
            if ev:
                events.add(ev)

    for k, v in EVENT_CANONICAL items():
        if k.isdigit():
            continue
        if re.search(rf"\b{re.escape(k)}\b", masked):
            events.add(v)

    events |= _expand_event_groups(_tokenize_phrases(q))
    return events

DECADE_OFFSETS = {"early": (0,4), "mid": (3,7), "late": (5,9)}

def _extract_year_range(q: str):
    low = q.lower().replace("’", "'")
    m = re.search(r"\b(20\d{2})\s*[-/]\s*(\d{2})\b", low)
    if m:
        y1 = int(m.group(1)); y2 = int(str(y1)[:2] + m.group(2)); return (min(y1,y2), max(y1,y2))
    m = re.search(r"(?:between|from)\s*((?:18|19|20)\d{2})\s*(?:and|to|-)\s*((?:18|19|20)\d{2})", low)
    if m:
        a, b = int(m.group(1)), int(m.group(2)); return (min(a,b), max(a,b))
    m = re.search(r"\b(before|prior to|earlier than|through|up to|by)\s*((?:18|19|20)\d{2})\b", low)
    if m:
        year = int(m.group(2)); return (None, year if m.group(1) in {"through","up to","by"} else year-1)
    m = re.search(r"\b(after|later than|since)\s*((?:18|19|20)\d{2})\b", low)
    if m:
        year = int(m.group(2)); return (year if m.group(1)=="since" else year+1, None)
    m = re.search(r"\bin\s*((?:18|19|20)\d{2})\b", low)
    if m:
        y = int(m.group(1)); return (y, y)
    m = re.search(r"\b(early|mid|late)?\s*((?:18|19|20)\d{2})s\b", low)
    if m:
        eml = (m.group(1) or "").strip().lower(); base = int(m.group(2)); start, end = base, base+9
        if eml in DECADE_OFFSETS:
            a,b = DECADE_OFFSETS[eml]; return (start+a, start+b)
        return (start, end)
    m = re.search(r"\b(early|mid|late)?\s*((?:18|19|20)\d)0s\b", low)
    if m:
        eml = (m.group(1) or "").strip().lower(); base = int(m.group(2))*10; start, end = base, base+9
        if eml in DECADE_OFFSETS:
            a,b = DECADE_OFFSETS[eml]; return (start+a, start+b)
        return (start, end)
    m = re.search(r"\b(early|mid|late)?\s*['']?(\d{2})s\b", low)
    if m:
        eml = (m.group(1) or "").strip().lower(); yy = int(m.group(2)); century = 1900 if yy>=30 else 2000
        start = century + (yy//10)*10; end = start+9
        if eml in DECADE_OFFSETS:
            a,b = DECADE_OFFSETS[eml]; return (start+a, start+b)
        return (start, end)
    yrs = [int(y) for y in re.findall(r"\b((?:18|19|20)\d{2})\b", low)]
    if len(yrs) == 1: return (yrs[0], yrs[0])
    if len(yrs) >= 2: return (min(yrs[:2]), max(yrs[:2]))
    return (None, None)

# ---- MISSING HELPER (added earlier): find targets (e.g., meets) in text ----

def _find_multi_targets(q: str, vocabulary: Dict[str, str]) -> List[str]:
    """
    Scans the query for known vocab keys (e.g., meet aliases) and returns
    a de-duplicated, order-preserving list of canonical values.
    """
    toks = _tokenize_phrases(q)
    found = []
    keys = sorted(vocabulary.keys(), key=len, reverse=True)  # longest first
    for t in toks:
        for k in keys:
            if k in t:
                found.append(vocabulary[k])
                break
    # de-dupe, preserve first-seen order
    seen = set()
    ordered = []
    for x in found:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    return ordered


def parse_question_multi(q: str) -> Dict[str, Optional[str]]:
    q_norm = q.replace("’", "'")
    out = {
        "intent": None, "scope": None,
        "genders": [], "events": set(), "meets": [],
        "schools": [], "athletes": [],
        "year_from": None, "year_to": None,
        "raw": q, "top_n": 10, "track_only": False,
        "maybe_school_phrases": []
    }
    low = q_norm.lower()

    # Personal Record
    if (re.search(r"\b(pr|pb)\b", low) or
        re.search(r"\bpersonal\s+(best|record)\b", low) or
        (("fastest" in low or "best" in low) and "'" in low)):
        out["intent"] = "personal_record"

    # Title Count (athlete total titles)
    if re.search(r"\bhow many\b.*\b(championships?|titles?)\b", low):
        out["intent"] = "count_titles"
        if "state" in low: out["scope"] = "state"
        if "indoor" in low: out["scope"] = "indoor"
        if "outdoor" in low: out["scope"] = "outdoor"

    # Pre-capture possible school phrases
    for m in re.finditer(r"\b([A-Z][A-Za-z.&'-]+(?:\s+[A-Z][A-Za-z.&'-]+)*)\s+(athlete|runner)\b", q_norm):
        out["maybe_school_phrases"].append(m.group(1).strip())
    for m in re.finditer(r"\b([A-Z][A-Za-z.&'-]+(?:\s+[A-Z][A-Za-z.&'-]+)*)\s+(team|program|school|squad|club)\b", q_norm):
        out["maybe_school_phrases"].append(m.group(1).strip())
    for m in re.finditer(r"\bwinner\s+from\s+([A-Z][A-Za-z.&'-]+(?:\s+[A-Z][A-Za-z.&'-]+)*)\b", q_norm):
        out["maybe_school_phrases"].append(m.group(1).strip())

    # School-win count (narrowed): require clear school phrase or preposition indicating a school, not generic "a"/"an"
    if (re.search(r"\bhow many\b.*\bwon\b", low) and
        (out["maybe_school_phrases"] or re.search(r"\b(from|for|by|of|out of|representing|with|at)\b", low))):
        out["intent"] = "count_school_titles"

    # Athlete-specific wins possible flag
    out["_athlete_win_possible"] = bool(re.search(r"\bhow many\b.*\b(times|wins?)\b.*\b(has|did)\b.*\bwon\b", low))

    # Leaderboard
    if (re.search(r"\b(most|record)\b.*\b(win|wins|won|titles?|races?)\b", low) or
        re.search(r"\b(win|wins|won|titles?|races?)\b.*\b(most|record)\b", low) or
        re.search(r"\btop\s+\d+\b", low)):
        if out["intent"] != "personal_record":
            out["intent"] = "leaderboard_wins"

    # State Records
    if re.search(r"\bstate\s+records?\b", low) or re.search(r"\b(indoor|outdoor)\s+state\s+records?\b", low):
        out["intent"] = "state_records_lookup"
        if "indoor" in low: out["scope"] = "indoor"
        elif "outdoor" in low: out["scope"] = "outdoor"

    # Last time
    if re.search(r"\bwhen was the last time\b.*\bwon\b", low) or re.search(r"\bmost recent\b.*\bwin\b", low):
        if out["intent"] != "personal_record":
            out["intent"] = "last_win_time"
    if re.search(r"\bwho was the last\b.*\bto win\b", low):
        if out["intent"] != "personal_record":
            out["intent"] = "last_win_time"

    # Progression / trend
    if (re.search(r"\bprogression\b", low) or re.search(r"\bover time\b", low) or
        re.search(r"\byear[- ]?by[- ]?year\b", low) or re.search(r"\bline (?:graph|chart)\b", low) or
        re.search(r"\btrend\b", low)):
        out["intent"] = "progression_chart"

    # Records / all-time lists
    if (re.search(r"\b(all[-\s]?time|top\s+times?|top\s+marks?|records?)\b", low) or
        re.search(r"\bfastest\b", low) or re.search(r"\bfurthest|longest\b", low) or
        re.search(r"\bbest\b.*\b(mark|time)\b", low)):
        if out["intent"] not in ("personal_record","state_records_lookup","progression_chart"):
            out["intent"] = "records_lookup"

    # Top-N
    m_top = re.search(r"\btop\s+(\d+)\b", low)
    if m_top:
        out["top_n"] = max(1, int(m_top.group(1)))
    if out["intent"] == "leaderboard_wins" and not m_top and re.search(r"\bwho\b", low) and re.search(r"\bmost\b", low):
        out["top_n"] = 1

    if "race" in low or "races" in low:
        out["track_only"] = True

    yf, yt = _extract_year_range(q_norm)
    out["year_from"], out["year_to"] = yf, yt

    # genders, meets, events
    g_tokens = []
    for tok in re.findall(r"[A-Za-z]+", q_norm):
        lt = tok.lower()
        if lt in GENDER_CANONICAL:
            g_tokens.append(GENDER_CANONICAL[lt])
    out["genders"] = sorted(set(g_tokens))

    out["meets"] = _find_multi_targets(low, MEET_CANONICAL)
    out["events"] = _extract_events_anywhere(q_norm)

    # >>> PRIORITIZE SWEEP COUNT WHEN MULTI-EVENT & NO ATHLETE NAME <<<
    if re.search(r"\bhow many\b.*\bwon\b", low) and len(out["events"]) >= 2 and not out["athletes"]:
        out["intent"] = "sweep_count"

    # schools and athletes
    for m in re.finditer(r"\b(?:from|for|by|of|out of|representing|with|at)\s+([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)+)\b", q_norm):
        out["schools"].append(m.group(1).strip())
    for m in re.finditer(r"\"([^\"]+)\"", q_norm):
        out["athletes"].append(m.group(1).strip())
    m = re.search(r"\b(?:has|did|for|by|from|at)\s+([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)+)", q_norm)
    if m:
        out["athletes"].append(m.group(1).strip())
    for m in re.finditer(r"\b([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)+)'s\b", q_norm):
        out["athletes"].append(m.group(1).strip())

    out["schools"]  = list(dict.fromkeys(out["schools"]))
    out["athletes"] = list(dict.fromkeys(out["athletes"]))

    # NEW: Sweep count intent — multiple events & no athlete name (kept, but priority was set above)
    if re.search(r"\bhow many\b.*\bwon\b", low) and len(out["events"]) >= 2 and not out["athletes"]:
        out["intent"] = "sweep_count"

    # Last sweep override when explicitly asking last/most recent
    if len(out["events"]) >= 2:
        if (out["intent"] in ("last_win_time",) or re.search(r"\b(last|most recent)\b", low) or re.search(r"\bto win\b", low)):
            out["intent"] = "last_sweep"

    return out

# ----------------------------
# Timeline & progression helpers
# ----------------------------

def add_numeric_mark_column(cur: pd.DataFrame) -> pd.DataFrame:
    def _parse_time_to_seconds(txt: str) -> Optional[float]:
        if txt is None:
            return None
        s = str(txt).strip()
        s = re.sub(r"[a-zA-Z]+$", "", s).strip()
        try:
            if ":" not in s:
                return float(s)
            parts = [p.strip() for p in s.split(":")]
            if len(parts) == 2:
                m, sec = parts
                return 60.0*float(m) + float(sec)
            if len(parts) == 3:
                h, m, sec = parts
                return 3600.0*float(h) + 60.0*float(m) + float(sec)
        except Exception:
            return None
        return None

    def _parse_distance_to_float(txt: str) -> Optional[float]:
        if txt is None:
            return None
        s = str(txt).strip().lower()
        s = re.sub(r"[a-z]+$", "", s).strip()
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$", s)
        if m:
            feet = float(m.group(1)); inches = float(m.group(2))
            return feet*12.0 + inches
        try:
            return float(s)
        except Exception:
            return None

    LOWER_BETTER = {"100/55","200","400","800","1600","3200","100/55H","110/55H","300H","4x100","4x200","4x400","4x800"}
    HIGHER_BETTER = {"LJ","TJ","HJ","PV","Shot put","Discus"}

    def _numeric(ev: str, mark: str):
        if ev in LOWER_BETTER: return _parse_time_to_seconds(mark)
        if ev in HIGHER_BETTER: return _parse_distance_to_float(mark)
        return None

    cur = cur.copy()
    cur["value"] = cur.apply(lambda r: _numeric(str(r["event"]), str(r["mark"])), axis=1)
    return cur


def plot_timeline_year_counts(df: pd.DataFrame, *, title: str, year_col: str = "year"):
    chart_df = (
        df.dropna(subset=[year_col])
          .groupby(year_col).size()
          .reset_index(name="count")
          .sort_values(year_col)
    )
    if chart_df.empty: return None
    c = alt.Chart(chart_df).mark_bar().encode(
        x=alt.X(f"{year_col}:O", title="Year"),
        y=alt.Y("count:Q", title="Count"),
        tooltip=[alt.Tooltip(f"{year_col}:O", title="Year"), alt.Tooltip("count:Q", title="Count")]
    ).properties(title=title, width="container")
    return c

# ----------------------------
# Streamlit UI
# ----------------------------

st.set_page_config(page_title="Delaware Online Sports Compendium", page_icon="🏃", layout="wide")
st.title("Delaware Online Sports Compendium")

# ---- Global CSS: left-align all table/dataframe content ----
st.markdown(
    """
    <style>
    /* st.table */
    [data-testid="stTable"] table,
    [data-testid="stTable"] th,
    [data-testid="stTable"] td {
        text-align: left !important;
    }
    /* st.dataframe (AG Grid) */
    [data-testid="stDataFrame"] div[role="gridcell"],
    [data-testid="stDataFrame"] div[role="columnheader"] {
        justify-content: flex-start !important;
        text-align: left !important;
    }
    [data-testid="stDataFrame"] .stDataFrame,
    [data-testid="stDataFrame"] table,
    [data-testid="stDataFrame"] td,
    [data-testid="stDataFrame"] th {
        text-align: left !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

APP_DIR = Path(__file__).parent
BUNDLED_XLSX_PATH = APP_DIR / "Delaware Track and Field Supersheet (6).xlsx"
BUNDLED_ALLTIME_XLSX_PATH = APP_DIR / "Personal All-Time List.xlsx"

INDOOR_ALLTIME_CANDIDATES = [
    "Personal All-Time List (Indoor).xlsx",
    "Personal All-Time List - Indoor.xlsx",
    "Personal All-Time List_Indoor.xlsx",
    "Indoor Personal All-Time List.xlsx",
]

with st.sidebar:
    st.header("Data sources")
    if not BUNDLED_XLSX_PATH.exists():
        st.error(f"Champions workbook not found at:\n{BUNDLED_XLSX_PATH}")
        df = None; mvps_df = None; KNOWN_SCHOOLS = set(); KNOWN_ATHLETES = set()
    else:
        try:
            with open(BUNDLED_XLSX_PATH, "rb") as f:
                file_bytes = f.read()
            df = load_champions(file_bytes)
            mvps_df = load_mvps(file_bytes)
            KNOWN_SCHOOLS  = set(df["school"].dropna().unique())
            KNOWN_ATHLETES = set(df["name"].dropna().unique())
            st.success(f"Champions: {len(df):,} rows")
            st.success(f"MVPs: {0 if mvps_df is None else len(mvps_df):,}")
        except Exception as ex:
            st.error("Error parsing champions workbook.")
            st.exception(ex)
            df = None; mvps_df = None; KNOWN_SCHOOLS = set(); KNOWN_ATHLETES = set()

    st.header("All‑Time / Records")
    alltime_df = None
    state_records_df = None
    alltime_df_indoor = None
    state_records_df_indoor = None

    if not BUNDLED_ALLTIME_XLSX_PATH.exists():
        st.warning(f"All‑time workbook not found at:\n{BUNDLED_ALLTIME_XLSX_PATH}")
    else:
        try:
            with open(BUNDLED_ALLTIME_XLSX_PATH, "rb") as f:
                at_bytes = f.read()
            alltime_df = load_alltime(at_bytes, "xlsx")
            state_records_df = load_state_records_first_sheet(at_bytes)
            st.success(f"All‑time rows (Outdoor): {len(alltime_df):,}")
            st.success(f"State records (Outdoor): {0 if state_records_df is None else len(state_records_df):,}")
        except Exception as ex:
            st.error("Error parsing outdoor all‑time/state records workbook.")
            st.exception(ex)
            alltime_df = None; state_records_df = None

    INDOOR_ALLTIME_XLSX_PATH = None
    for cand in INDOOR_ALLTIME_CANDIDATES:
        p = APP_DIR / cand
        if p.exists():
            INDOOR_ALLTIME_XLSX_PATH = p
            break

    if INDOOR_ALLTIME_XLSX_PATH:
        try:
            with open(INDOOR_ALLTIME_XLSX_PATH, "rb") as f:
                at_in_bytes = f.read()
            alltime_df_indoor = load_alltime(at_in_bytes, "xlsx")
            state_records_df_indoor = load_state_records_first_sheet(at_in_bytes)
            st.success(f"All‑time rows (Indoor): {len(alltime_df_indoor):,}")
            st.success(f"State records (Indoor): {0 if state_records_df_indoor is None else len(state_records_df_indoor):,}")
        except Exception as ex:
            st.warning("Indoor all‑time/state records workbook detected but failed to parse.")
            st.exception(ex)
            alltime_df_indoor = None
            state_records_df_indoor = None

# Tabs
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "🔎 Ask a question", "🎛️ Explore", "👤 Athlete profiles",
    "🏆 MVPs", "🛠️ Data status", "🏅 State Records", "🥇 All‑Time Lists"
])

# ----------------------------
# Helpers (deep-link support for Q if needed)
# ----------------------------

def _get_q_from_url():
    try:
        params = st.query_params
        if "q" in params:
            val = params["q"]
            return val[0] if isinstance(val, list) else val
    except Exception:
        try:
            params = st.experimental_get_query_params()
            if "q" in params:
                val = params["q"]
                return val[0] if isinstance(val, list) else val
        except Exception:
            pass
    return ""


def _set_q_in_url(qval: str):
    try:
        st.query_params["q"] = qval
    except Exception:
        try:
            st.experimental_set_query_params(q=qval)
        except Exception:
            pass

# ----------------------------
# Q&A
# ----------------------------
with tab1:
    st.caption("Ask about champions, leaderboards, MVPs, last wins, **state records** (Outdoor by default; add 'indoor' to switch), **PR/PB**, and all‑time lists.")

    if "q_prefill" not in st.session_state or not st.session_state["q_prefill"]:
        url_q = _get_q_from_url()
        if url_q:
            st.session_state["q_prefill"] = url_q

    example_prompts = [
        "Who won the boys 200 at the indoor state meet in 2026?",
        "How many state championships did Juliana Balon win?",
        "How many times has a Tatnall athlete won the girls 800 at the Meet of Champions?",
        "How many times has a girl won the 800, 1600 and 3200 at the indoor state meet?",
        "Who has won the most New Castle County Championships races?",
    ]
    st.caption("Quick examples:")
    ex_cols = st.columns(2)
    for i, ex in enumerate(example_prompts):
        if ex_cols[i % 2].button(ex):
            st.session_state["q_prefill"] = ex
            _set_q_in_url(ex)
            st.rerun()

    c_q, c_btn = st.columns([0.8, 0.2])
    prefill = st.session_state.get("q_prefill", "")
    q = c_q.text_input("Type your question", value=prefill)

    def _clear_q():
        st.session_state["q_prefill"] = ""
        try:
            st.query_params.clear()
        except Exception:
            try:
                st.experimental_set_query_params()
            except Exception:
                pass
        st.rerun()

    if c_btn.button("Clear question"):
        _clear_q()
    if q:
        _set_q_in_url(q)

    def _render_understood(fm: Dict[str, Optional[str]]):
        parts = []
        if fm.get("genders"): parts.append("**Gender:** " + ", ".join([g.title() for g in fm["genders"]]))
        if fm.get("events"): parts.append("**Events:** " + ", ".join(sorted(fm["events"])) )
        if fm.get("meets"): parts.append("**Meets:** " + ", ".join(fm["meets"]))
        if fm.get("schools"): parts.append("**Schools (hints):** " + ", ".join(fm["schools"]))
        if fm.get("athletes"): parts.append("**Athletes:** " + ", ".join(fm["athletes"]))
        yf, yt = fm.get("year_from"), fm.get("year_to")
        if yf and yt and yf == yt: parts.append(f"**Year:** {yf}")
        elif yf and yt: parts.append(f"**Years:** {yf}–{yt}")
        elif yf and not yt: parts.append(f"**Since:** {yf}")
        intent = fm.get("intent")
        if intent:
            label = {
                "leaderboard_wins": "Leaderboard",
                "count_titles": "Title Count",
                "count_school_titles": "School‑win Count",
                "count_athlete_wins": "Athlete‑win Count",
                "mvp_lookup": "MVP Lookup",
                "last_win_time": "Last Time",
                "last_sweep": "Last Sweep",
                "sweep_count": "Sweep Count",
                "records_lookup": "All‑Time / Records",
                "state_records_lookup": "State Records",
                "personal_record": "Personal Record",
                "progression_chart": "Progression Chart",
            }.get(intent, intent)
            parts.insert(0, f"**Intent:** {label}")
        if parts:
            st.markdown("🧠 *What I understood:* " + " — ".join(parts))

    # Newark aware school filler + extended patterns
    def _auto_add_schools(fm: Dict[str, Optional[str]]):
        if 'schools_exact' not in fm:
            fm['schools_exact'] = []
        if 'KNOWN_SCHOOLS' not in globals() or not KNOWN_SCHOOLS:
            return
        raw = fm.get('raw') or ''
        lowq = raw.lower()

        # Exact hits
        for s in KNOWN_SCHOOLS:
            if isinstance(s, str) and s.strip() and s.lower() in lowq:
                fm['schools_exact'].append(s)

        has_newark_charter = 'newark charter' in lowq
        has_newark = bool(re.search(r'\bnewark\b', lowq))
        if has_newark and not has_newark_charter:
            targets = []
            for candidate in ['Newark', 'Newark High']:
                if candidate in KNOWN_SCHOOLS:
                    targets.append(candidate)
            if targets:
                fm['schools_exact'].extend(targets)

        # <School> athlete|runner
        for m in re.finditer(r"\b([A-Z][A-Za-z.&'-]+(?:\s+[A-Z][A-Za-z.&'-]+)*)\s+(athlete|runner)\b", raw):
            candidate = m.group(1).strip()
            if any(candidate.lower() in str(s).lower() for s in KNOWN_SCHOOLS):
                fm.setdefault('schools', []); fm['schools'].append(candidate)
        # <School> team/program/school/squad/club
        for m in re.finditer(r"\b([A-Z][A-Za-z.&'-]+(?:\s+[A-Z][A-Za-z.&'-]+)*)\s+(team|program|school|squad|club)\b", raw):
            candidate = m.group(1).strip()
            if any(candidate.lower() in str(s).lower() for s in KNOWN_SCHOOLS):
                fm.setdefault('schools', []); fm['schools'].append(candidate)
        # winner from <School>
        for m in re.finditer(r"\bwinner\s+from\s+([A-Z][A-Za-z.&'-]+(?:\s+[A-Z][A-Za-z.&'-]+)*)\b", raw):
            candidate = m.group(1).strip()
            if any(candidate.lower() in str(s).lower() for s in KNOWN_SCHOOLS):
                fm.setdefault('schools', []); fm['schools'].append(candidate)
        # preposition-based school capture
        for m in re.finditer(r"\b(?:from|for|by|of|out of|representing|with|at)\s+([A-Z][A-Za-z.&'-]+(?:\s+[A-Z][A-Za-z.&'-]+)*)\b", raw):
            candidate = m.group(1).strip()
            if any(candidate.lower() in str(s).lower() for s in KNOWN_SCHOOLS):
                fm.setdefault('schools', []); fm['schools'].append(candidate)

        if not fm['schools_exact'] and not fm.get('schools'):
            tokens = re.findall(r"\b([A-Z][A-Za-z.&'-]+)\b", raw)
            for tok in tokens:
                if tok.lower() == 'newark' and not has_newark_charter:
                    continue
                if any(tok.lower() in str(s).lower() for s in KNOWN_SCHOOLS):
                    fm.setdefault('schools', []); fm['schools'].append(tok)

        fm['schools_exact'] = list(dict.fromkeys([s for s in fm['schools_exact'] if isinstance(s, str) and s.strip()]))
        if fm.get('schools'):
            fm['schools'] = list(dict.fromkeys([s for s in fm['schools'] if isinstance(s, str) and s.strip()]))

    def _apply_state_default(fm: Dict[str, Optional[str]], text: str):
        lowx = text.lower()
        # 'state meet' mention → consider both Indoor & Outdoor if nothing else set
        if ("state meet" in lowx or ("state" in lowx and "meet" in lowx)) and not fm["meets"]:
            fm["meets"] = list(STATE_MEETS_ALL)
        # If 'state' w/o season and no meets yet, default to all state meets
        if ("state" in lowx) and ("indoor" not in lowx) and ("outdoor" not in lowx) and not fm["meets"]:
            fm["meets"] = list(STATE_MEETS_ALL)
        # Heuristic: indoor-coded events & no meets → choose indoor state
        if ({"100/55","100/55H","110/55H"} & set(fm.get("events", []))) and not fm["meets"]:
            fm["meets"] = list(STATE_MEETS_INDOOR)

    if q and df is not None:
        f_multi = parse_question_multi(q)

        # Prefer school-win over athlete-win when ambiguous — but do NOT override sweep_count
        _auto_add_schools(f_multi)
        # Guard: If sweep_count already chosen, don't override
        if f_multi.get("intent") == "sweep_count":
            pass
        elif f_multi.get("_athlete_win_possible"):
            if f_multi.get("athletes"):
                f_multi["intent"] = "count_athlete_wins"
            else:
                if f_multi.get("schools") or f_multi.get("schools_exact") or f_multi.get("maybe_school_phrases"):
                    f_multi["intent"] = "count_school_titles"

        _apply_state_default(f_multi, f_multi["raw"])
        _render_understood(f_multi)

        # --- If user said just "state meet", offer a quick picker to choose which state meet(s)
        low_raw = f_multi["raw"].lower()
        try_show_state_picker = (
            ("state" in low_raw and "meet" in low_raw) and
            ("indoor" not in low_raw) and ("outdoor" not in low_raw) and
            (not any(x in low_raw for x in ["division i","division 1","division ii","division 2","d1","d2"])) and
            set(f_multi.get("meets") or []) == STATE_MEETS_ALL
        )
        if try_show_state_picker:
            st.info("You said **state meet** — which one did you mean?")
            meet_options = ["Indoor State Championship", "Division I", "Division II"]
            picks = st.multiselect(
                "Choose state meet(s)", options=meet_options, default=meet_options,
                help="Pick Indoor and/or an Outdoor division."
            )
            if picks:
                f_multi["meets"] = picks

        # ------------- Handlers -------------

        # Title count
        if f_multi.get("intent") == "count_titles":
            if 'KNOWN_ATHLETES' in globals() and KNOWN_ATHLETES and not f_multi["athletes"]:
                lowp = f_multi["raw"].lower()
                candidates = [n for n in KNOWN_ATHLETES if isinstance(n, str) and n.lower() in lowp]
                if candidates:
                    f_multi["athletes"] = sorted(set(candidates), key=lambda s: (-len(s), s))
            if not f_multi["athletes"]:
                st.info('I couldn’t identify the athlete’s name. Try quoting it, e.g., “How many state championships has "Juliana Balon" won?”')
                st.stop()
            scope = f_multi.get("scope")
            if scope == "indoor": include_meets = STATE_MEETS_INDOOR
            elif scope == "outdoor": include_meets = STATE_MEETS_OUTDOOR
            else: include_meets = STATE_MEETS_ALL if ("state" in (f_multi.get("scope") or "") or "state" in q.lower()) else set(df["meet"].unique())
            athlete = f_multi["athletes"][0]
            genders_to_check = f_multi["genders"] if f_multi["genders"] else guess_gender_for_name(df, athlete)
            df_scope = df[df["gender"].isin(genders_to_check)]
            total_count, rows = title_count(df_scope, athlete, include_meets=include_meets, include_relays=False)
            c_name, c_metric = st.columns([0.6, 0.4])
            with c_name: st.markdown(f"<div style='font-size:1.6rem;font-weight:700;line-height:1.2'>{athlete}</div>", unsafe_allow_html=True)
            with c_metric: metric_row([("Titles", str(total_count))])
            if total_count > 0:
                timeline_chart = plot_timeline_year_counts(rows[["year"]].dropna(), title="Titles by year")
                if timeline_chart is not None: st.altair_chart(timeline_chart, use_container_width=True)
                c1,c2,c3 = st.columns(3)
                with c1: st.caption("By meet");  show_table(rows.groupby("meet").size().reset_index(name="titles"))
                with c2: st.caption("By event"); show_table(rows.groupby("event").size().reset_index(name="titles"))
                with c3: st.caption("By year");  show_table(rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
                st.caption("Title rows (relays excluded)")
                show_table(rows[["gender","year","meet","event","class","school","mark"]])
            st.stop()

        # Athlete-specific win count
        if f_multi.get("intent") == "count_athlete_wins":
            if not f_multi.get("athletes"):
                f_multi["intent"] = "count_school_titles"  # safety downgrade
            else:
                athlete = f_multi["athletes"][0]
                if not f_multi["events"]: st.info("Please specify the event (e.g., 'boys 400')."); st.stop()
                if not f_multi["meets"]:  st.info("Please specify the meet (e.g., 'Meet of Champions')."); st.stop()
                genders_to_check = f_multi["genders"] if f_multi["genders"] else guess_gender_for_name(df, athlete)
                cur = df.copy()
                cur["name_norm"] = cur["name"].apply(normalize_name)
                athlete_norm = normalize_name(athlete)
                cur = cur[cur["name_norm"] == athlete_norm]
                cur = cur[cur["gender"].isin(set(genders_to_check))]
                cur = cur[cur["event"].isin(set(f_multi["events"]))]
                cur = cur[cur["meet"].isin(set(f_multi["meets"]))]
                yf, yt = f_multi.get("year_from"), f_multi.get("year_to")
                cur["year_num"] = pd.to_numeric(cur.get("year"), errors="coerce")
                if yf and yt: cur = cur[(cur["year_num"] >= yf) & (cur["year_num"] <= yt)]
                elif yf and not yt: cur = cur[cur["year_num"] >= yf]
                total = len(cur)
                metric_row([("Wins", str(total))])
                if total > 0:
                    st.caption("Matched wins")
                    show_table(cur[["gender","year","meet","event","name","school","class","mark"]].sort_values(["year","event"], ascending=[False, True]))
                else:
                    st.info("No wins found matching the athlete + event + meet filters.")
                st.stop()

        # School-win count
        if f_multi.get("intent") == "count_school_titles":
            if not f_multi["events"]: st.info("Please specify the event (e.g., 'boys 400')."); st.stop()
            if not f_multi["meets"]:  st.info("Please specify the meet (e.g., 'Meet of Champions')."); st.stop()
            if not f_multi["genders"]: st.info("Please specify the gender (e.g., 'boys' or 'girls')."); st.stop()
            if not (f_multi.get("schools_exact") or f_multi.get("schools") or f_multi.get("maybe_school_phrases")):
                st.info("Please mention the school/city (e.g., 'a Newark athlete')."); st.stop()
            def apply_multi_filters(df_: pd.DataFrame, f):
                cur = df_.copy(); cur["year_num"] = pd.to_numeric(cur.get("year"), errors="coerce")
                if f["genders"]: cur = cur[cur["gender"].isin(set(f["genders"]))]
                if f["meets"]:   cur = cur[cur["meet"].isin(set(f["meets"]))]
                if f["events"]:  cur = cur[cur["event"].isin(set(f["events"]))]
                if f.get('schools_exact'): cur = cur[cur['school'].isin(set(f['schools_exact']))]
                elif f.get('schools'):
                    mask = pd.Series(False, index=cur.index)
                    for s in f['schools']:
                        needle = s.lower(); mask = mask | cur['school'].str.lower().str.contains(needle, na=False)
                    cur = cur[mask]
                elif f.get('maybe_school_phrases'):
                    mask = pd.Series(False, index=cur.index)
                    for s in f['maybe_school_phrases']:
                        needle = s.lower(); mask = mask | cur['school'].str.lower().str.contains(needle, na=False)
                    cur = cur[mask]
                yf, yt = f.get("year_from"), f.get("year_to")
                if yf and yt: cur = cur[(cur["year_num"] >= yf) & (cur["year_num"] <= yt)]
                elif yf and not yt: cur = cur[cur["year_num"] >= yf]
                return cur.sort_values(["gender","event","meet","year"], ascending=[True,True,True,False])
            cur = apply_multi_filters(df, f_multi)
            cur = cur[~cur["event"].isin(EVENT_GROUPS["relays"])]
            total = len(cur); metric_row([("Wins", str(total))])
            if cur.empty:
                st.warning("No matching wins for that school filter + event/meet/gender.")
                with st.expander("Detected filters"): st.json(f_multi)
                st.stop()
            st.caption("Wins by school"); show_table(cur.groupby("school").size().reset_index(name="wins").sort_values("wins", ascending=False))
            st.caption("Matched wins"); show_table(cur[["gender","year","meet","event","name","school","class","mark"]].sort_values(["year","meet","event"], ascending=[False, True, True]))
            st.stop()

        # ---- Leaderboard (wins) ----
        if f_multi.get("intent") == "leaderboard_wins":
            _apply_state_default(f_multi, f_multi["raw"])
            _auto_add_schools(f_multi)
            lb = leaderboard_wins(df, f_multi)
            if lb is None or lb.empty:
                st.error("No matching winners found for your leaderboard filters.")
                with st.expander("Detected filters"):
                    st.json(f_multi)
                st.stop()
            if f_multi.get("top_n", 10) == 1:
                r = lb.iloc[0]
                with st.container(border=True):
                    st.markdown(f"**{r['name']}** — {r['school']}")
                    st.markdown(f"🏆 **{int(r['wins'])}** wins")
                cur = _apply_filters_for_leaderboard(df, f_multi)
                wins_rows = cur[(cur["name"] == r["name"]) & (cur["school"] == r["school"]) & (cur["gender"] == r.get("gender", cur.get("gender")))]
                wins_rows = wins_rows.sort_values(["year","meet","event"], ascending=[False, True, True])
                if not wins_rows.empty:
                    st.caption("That athlete’s wins (matching your filters)")
                    show_table(wins_rows[["gender","year","meet","event","class","school","mark"]])
                st.stop()
            else:
                show_table(lb.reset_index(drop=True)[[c for c in ["gender","name","school","wins"] if c in lb.columns]])
                st.stop()

        # NEW: Sweep count
        if f_multi.get("intent") == "sweep_count":
            lowp = f_multi["raw"].lower()
            if not f_multi["meets"]:
                if "indoor" in lowp: f_multi["meets"] = list(STATE_MEETS_INDOOR)
                elif "outdoor" in lowp: f_multi["meets"] = list(STATE_MEETS_OUTDOOR)
                elif "state" in lowp or "state meet" in lowp: f_multi["meets"] = list(STATE_MEETS_ALL)
            required_events = set(f_multi["events"])
            if len(required_events) < 2:
                st.info("Please specify at least two events for a sweep (e.g., 800, 1600 and 3200)."); st.stop()
            if not f_multi["genders"]:
                st.info("Please specify the gender (e.g., 'girls' or 'boys')."); st.stop()
            cur = df.copy()
            cur = cur[cur["gender"].isin(f_multi["genders"]) ]
            cur = cur[cur["meet"].isin(f_multi["meets"]) ]
            cur = cur[cur["event"].isin(required_events)]
            cur = cur[~cur["event"].isin(EVENT_GROUPS["relays"])]
            yf, yt = f_multi.get("year_from"), f_multi.get("year_to")
            cur["year_num"] = pd.to_numeric(cur.get("year"), errors="coerce")
            if yf and yt: cur = cur[(cur["year_num"] >= yf) & (cur["year_num"] <= yt)]
            elif yf and not yt: cur = cur[cur["year_num"] >= yf]
            if cur.empty:
                st.info("Zero occurrences found."); st.stop()
            agg = cur.groupby(["year","meet","gender","name"])['event'].apply(set).reset_index(name="events_won")
            sweeps = agg[agg["events_won"].apply(lambda s: required_events.issubset(s))]
            if sweeps.empty:
                st.info("Zero occurrences found."); st.stop()
            occurrences = sweeps.groupby(["year","meet","gender"]).size().reset_index(name="count")
            total_times = len(occurrences)
            years = sorted(occurrences['year'].dropna().astype(int).unique().tolist())
            yr_txt = ", ".join(str(y) for y in years[:25]) + (" …" if len(years) > 25 else "")
            gender_label = "/".join([g.title() for g in f_multi["genders"]])
            meet_label = ", ".join(f_multi["meets"]) if f_multi["meets"] else "the selected meets"
            ev_label = ", ".join(sorted(required_events))
            st.markdown(f"**Summary:** It has happened **{total_times}** time(s) for **{gender_label}** at **{meet_label}** in **{ev_label}**. Years: {yr_txt}")
            metric_row([("Times", str(total_times))])
            detail = cur.merge(sweeps[["year","meet","gender","name"]], on=["year","meet","gender","name"], how="inner")
            detail = detail.sort_values(["year","name","event"])  # events rows visible
            st.caption("Each sweep (with events that year)")
            show_table(detail[["gender","year","meet","name","school","event","mark"]])
            st.caption("Sweeps by athlete (count of years)")
            by_ath = sweeps.groupby(["gender","name"]).size().reset_index(name="years_swept").sort_values(["years_swept","name"], ascending=[False, True])
            show_table(by_ath)
            st.stop()

        # Last time
        if f_multi.get("intent") == "last_win_time":
            lowp = f_multi["raw"].lower()
            if not f_multi["meets"]:
                if "indoor" in lowp: f_multi["meets"] = list(STATE_MEETS_INDOOR)
                elif "outdoor" in lowp: f_multi["meets"] = list(STATE_MEETS_OUTDOOR)
                elif "state" in lowp: f_multi["meets"] = list(STATE_MEETS_ALL)
            def apply_multi_filters(df_: pd.DataFrame, f):
                cur = df_.copy(); cur["year_num"] = pd.to_numeric(cur.get("year"), errors="coerce")
                if f["genders"]: cur = cur[cur["gender"].isin(set(f["genders"]))]
                if f["meets"]:   cur = cur[cur["meet"].isin(set(f["meets"]))]
                if f["events"]:  cur = cur[cur["event"].isin(set(f["events"]))]
                yf, yt = f.get("year_from"), f.get("year_to")
                if yf and yt: cur = cur[(cur["year_num"] >= yf) & (cur["year_num"] <= yt)]
                elif yf and not yt: cur = cur[cur["year_num"] >= yf]
                if f.get("track_only"): cur = cur[cur["event"].isin(TRACK_EVENTS)]
                return cur.sort_values(["gender","event","meet","year"], ascending=[True,True,True,False])
            cur = apply_multi_filters(df, f_multi)
            cur = cur[~cur["event"].isin(EVENT_GROUPS["relays"])]
            if cur.empty:
                st.error("No matching winner. Try specifying gender/event/meet more precisely.")
                with st.expander("Detected filters"): st.json(f_multi)
                st.stop()
            latest_year = int(cur["year"].max())
            latest_rows = cur[cur["year"] == latest_year].sort_values(["gender","meet","event"])
            r0 = latest_rows.iloc[0]
            with st.container(border=True):
                st.markdown(f"**{str(r0['gender']).title()} — {r0['event']} — {r0['meet']}**")
                st.markdown(f"- **Year:** {int(r0['year']) if pd.notna(r0['year']) else ''}")
                st.markdown(f"- **Athlete:** {r0['name']}")
                st.markdown(f"- **Time/Mark:** {r0['mark']}")
                st.markdown(f"- **School:** {r0['school']}")
            if len(latest_rows) > 1:
                st.caption("All matching winners in that year"); show_table(latest_rows[["gender","year","meet","event","name","school","class","mark"]])
            st.stop()

        # Last sweep
        if f_multi.get("intent") == "last_sweep":
            lowp = f_multi["raw"].lower()
            if not f_multi["meets"]:
                if "indoor" in lowp: f_multi["meets"] = list(STATE_MEETS_INDOOR)
                elif "outdoor" in lowp: f_multi["meets"] = list(STATE_MEETS_OUTDOOR)
                elif "state" in lowp: f_multi["meets"] = list(STATE_MEETS_ALL)
            required_events = set(f_multi["events"])
            if len(required_events) < 2:
                st.info("Please specify at least two events for a sweep check (e.g., 800, 1600 and 3200)."); st.stop()
            cur = df.copy()
            if f_multi["genders"]: cur = cur[cur["gender"].isin(f_multi["genders"]) ]
            if f_multi["meets"]:   cur = cur[cur["meet"].isin(f_multi["meets"]) ]
            cur = cur[cur["event"].isin(required_events)]
            cur = cur[~cur["event"].isin(EVENT_GROUPS["relays"])]
            if cur.empty:
                st.error("No matches for the specified sweep filters.")
            else:
                agg = (cur.groupby(["year","meet","gender","name"])['event'].apply(set).reset_index(name="events_won"))
                sweeps = agg[agg["events_won"].apply(lambda s: required_events.issubset(s))]
                if sweeps.empty:
                    st.warning("No athlete found who swept all those events at a single meet.")
                    with st.expander("Detected filters"): st.json(f_multi)
                    st.stop()
                last_year = int(sweeps["year"].max())
                last_hits = sweeps[sweeps["year"] == last_year].sort_values(["gender","meet","name"])
                winners = ", ".join(sorted(last_hits["name"].unique()))
                metric_row([("Year", str(last_year)), ("Winner(s)", winners)])
                detail = cur[cur["year"] == last_year]
                detail = detail.merge(last_hits[["year","meet","gender","name"]], on=["year","meet","gender","name"], how="inner")
                detail = detail.sort_values(["gender","meet","name","event"])
                show_table(detail[["gender","year","meet","name","event","school","class","mark"]])
            st.stop()

        # Progression
        if f_multi.get("intent") == "progression_chart":
            if not f_multi["events"]: st.info("Please specify the event (e.g., 'boys 400', 'girls 1600')."); st.stop()
            if not f_multi["meets"]:  st.info("Please specify the meet (e.g., 'Meet of Champions', 'Division II')."); st.stop()
            def apply_multi_filters(df_: pd.DataFrame, f):
                cur = df_.copy(); cur["year_num"] = pd.to_numeric(cur.get("year"), errors="coerce")
                if f["genders"]: cur = cur[cur["gender"].isin(set(f["genders"]))]
                if f["meets"]:   cur = cur[cur["meet"].isin(set(f["meets"]))]
                if f["events"]:  cur = cur[cur["event"].isin(set(f["events"]))]
                yf, yt = f.get("year_from"), f.get("year_to")
                if yf and yt: cur = cur[(cur["year_num"] >= yf) & (cur["year_num"] <= yt)]
                elif yf and not yt: cur = cur[cur["year_num"] >= yf]
                if f.get("track_only"): cur = cur[cur["event"].isin(TRACK_EVENTS)]
                return cur.sort_values(["gender","event","meet","year"], ascending=[True,True,True,False])
            cur = apply_multi_filters(df, f_multi)
            cur = cur[~cur["event"].isin(EVENT_GROUPS["relays"])]
            if cur.empty:
                st.warning("No matching winners to plot. Try adjusting event/meet/gender/years.")
                with st.expander("Detected filters"): st.json(f_multi)
                st.stop()
            cur = add_numeric_mark_column(cur)
            cur = cur.dropna(subset=["year", "value"]).sort_values(["event","gender","year"])
            title_bits = []
            if f_multi["genders"]: title_bits.append("/".join([g.title() for g in f_multi["genders"]]))
            if f_multi["events"]:  title_bits.append(", ".join(sorted(f_multi["events"])) )
            if f_multi["meets"]:   title_bits.append(", ".join(f_multi["meets"]))
            yf, yt = f_multi.get("year_from"), f_multi.get("year_to")
            if yf and yt and yf == yt: title_bits.append(f"{yf}")
            elif yf and yt:            title_bits.append(f"{yf}–{yt}")
            elif yf:                   title_bits.append(f"since {yf}")
            chart_title = " • ".join([s for s in title_bits if s])
            chart = alt.Chart(cur).mark_line(point=True).encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("value:Q", title="Time (seconds)" if all(e in {"100/55","200","400","800","1600","3200","100/55H","110/55H","300H","4x100","4x200","4x400","4x800"} for e in set(cur["event"])) else "Mark (numeric)",
                         scale=alt.Scale(reverse=all(e in {"100/55","200","400","800","1600","3200","100/55H","110/55H","300H","4x100","4x200","4x400","4x800"} for e in set(cur["event"])))),
                color=alt.Color("event:N" if cur["event"].nunique()>1 else "meet:N" if cur["meet"].nunique()>1 else "gender:N"),
                tooltip=["year:O","event:N","gender:N","meet:N","name:N","school:N","mark:N"],
            ).properties(title=chart_title or "Progression over time", width="container")
            st.altair_chart(chart, use_container_width=True)
            st.caption("Data points used for the chart"); show_table(cur[["gender","event","meet","year","name","school","mark"]])
            st.stop()

        # Records / All-time lists
        if f_multi.get("intent") == "records_lookup":
            if alltime_df is None or alltime_df.empty:
                st.error("All‑time workbook not loaded. Add it to the repo to use records queries.")
                with st.expander("Detected filters"): st.json(f_multi)
                st.stop()
            genders = f_multi.get("genders") or sorted(alltime_df["gender"].dropna().unique().tolist())
            events  = sorted(f_multi.get("events") or [])
            meets   = f_multi.get("meets") or []
            yf, yt  = f_multi.get("year_from"), f_multi.get("year_to")
            if not events: st.info("Please specify at least one event for all‑time records, e.g., “girls 400 all‑time”."); st.stop()
            cur = alltime_df.copy()
            cur = cur[cur["gender"].isin(genders)]
            cur = cur[cur["event"].isin(events)]
            if meets: cur = cur[cur["meet"].isin(meets)]
            if yf and yt: cur = cur[(cur["year"].astype("float") >= yf) & (cur["year"].astype("float") <= yt)]
            elif yf and not yt: cur = cur[cur["year"].astype("float") >= yf]
            if cur.empty:
                st.error("No all‑time entries matched your filters.")
                with st.expander("Detected filters"): st.json(f_multi)
                st.stop()
            topn = 1 if (re.search(r"\bwho\b", f_multi["raw"].lower()) and len(events) == 1) else 10
            m_top = re.search(r"\btop\s+(\d+)\b", f_multi["raw"].lower())
            if m_top: topn = max(1, int(m_top.group(1)))
            outs = []
            for g in genders:
                for ev in events:
                    chunk = cur[(cur["gender"] == g) & (cur["event"] == ev)].sort_values("__sortkey", ascending=True).head(topn)
                    if not chunk.empty:
                        outs.append(chunk)
            if not outs:
                st.error("No all‑time results to display."); st.stop()
            res = pd.concat(outs, ignore_index=True)
            st.dataframe(res[[c for c in ["gender","event","mark","name","school","year","meet","location"] if c in res.columns]], use_container_width=True, hide_index=True)
            st.stop()

        # Fallback
        def apply_multi_filters(df_: pd.DataFrame, f):
            cur = df_.copy(); cur["year_num"] = pd.to_numeric(cur.get("year"), errors="coerce")
            if f["genders"]: cur = cur[cur["gender"].isin(set(f["genders"]))]
            if f["meets"]:   cur = cur[cur["meet"].isin(set(f["meets"]))]
            if f["events"]:  cur = cur[cur["event"].isin(set(f["events"]))]
            if f.get('schools_exact'): cur = cur[cur['school'].isin(set(f['schools_exact']))]
            elif f.get('schools'):
                mask = pd.Series(False, index=cur.index)
                for s in f['schools']:
                    needle = s.lower(); mask = mask | cur['school'].str.lower().str.contains(needle, na=False)
                cur = cur[mask]
            yf, yt = f.get("year_from"), f.get("year_to")
            if yf and yt: cur = cur[(cur["year_num"] >= yf) & (cur["year_num"] <= yt)]
            elif yf and not yt: cur = cur[cur["year_num"] >= yf]
            if f.get("track_only"): cur = cur[cur["event"].isin(TRACK_EVENTS)]
            return cur.sort_values(["gender","event","meet","year"], ascending=[True,True,True,False])

        result = apply_multi_filters(df, f_multi)
        if result.empty:
            st.error("No matches found. Try adjusting events/meets/schools/years.")
            with st.expander("Detected filters"): st.json(f_multi)
        else:
            if (f_multi.get("year_from") == f_multi.get("year_to") and len(f_multi.get("events", [])) == 1 and len(result) == 1):
                row = result.iloc[0]
                with st.container(border=True):
                    st.markdown(f"**{str(row['gender']).title()} {row['event']} — {row['meet']} {row['year']}**")
                    st.markdown(f"- **Winner:** {row['name']}")
                    st.markdown(f"- **Class:** {str(row.get('class',''))}")
                    st.markdown(f"- **School:** {row['school']}")
                    st.markdown(f"- **Mark:** {row['mark']}")
            show_table(result[["gender","event","meet","year","name","class","school","mark"]])

# ----------------------------
# Explore
# ----------------------------
with tab2:
    st.subheader("Filter champions")
    if df is None:
        st.info("Bundled workbook not loaded.")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        g   = c1.selectbox("Gender", options=["(any)"] + sorted(df["gender"].unique().tolist()))
        m   = c2.selectbox("Meet", options=["(any)"] + sorted(df["meet"].unique().tolist()))
        ev_all = sorted(df["event"].unique().tolist())
        ev  = c3.selectbox("Event", options=["(any)"] + ev_all)
        yrs = sorted(df["year"].dropna().unique().tolist(), reverse=True)
        y   = c4.selectbox("Year", options=["(any)"] + yrs)
        who = c5.text_input("Athlete / School contains")
        cur = df.copy()
        if g != "(any)": cur = cur[cur["gender"] == g]
        if m != "(any)": cur = cur[cur["meet"] == m]
        if ev != "(any)": cur = cur[cur["event"] == ev]
        if y != "(any)": cur = cur[cur["year"] == y]
        if who:
            needle = who.lower()
            cur = cur[ cur["name"].str.lower().str.contains(needle, na=False) | cur["school"].str.lower().str.contains(needle, na=False) ]
        st.metric("Matching champions", f"{len(cur):,}")
        show_table(cur.sort_values(["gender","event","meet","year"], ascending=[True,True,True,False]).reset_index(drop=True))

# ----------------------------
# Athlete Profiles (relays excluded)
# ----------------------------
with tab3:
    st.subheader("Athlete profiles (relays excluded)")
    if df is None:
        st.info("No data loaded.")
    else:
        idx = all_athletes_index(df)
        names_list = idx["name"].dropna().unique().tolist()
        options = ["(type to search)"] + names_list
        athlete = st.selectbox("Choose athlete", options=options, index=0)
        scope_options = ["All meets", "State (Indoor + Division I/II)", "Indoor only", "Outdoor only"]
        scope = st.radio("Scope", options=scope_options, horizontal=True, index=0)
        if athlete and athlete != "(type to search)":
            if scope == "Indoor only": include_meets = STATE_MEETS_INDOOR; scope_label = "Indoor State Championship"
            elif scope == "Outdoor only": include_meets = STATE_MEETS_OUTDOOR; scope_label = "Outdoor (Division I & II)"
            elif scope == "All meets": include_meets = set(df["meet"].unique()); scope_label = "All meets"
            else: include_meets = STATE_MEETS_ALL; scope_label = "State (Indoor + Division I/II)"
            genders = guess_gender_for_name(df, athlete)
            collected = []
            for g in genders:
                count, rows = title_count(df[df["gender"] == g], athlete, include_meets=include_meets, include_relays=False)
                collected.append((g, count, rows))
            st.markdown(f"### {athlete} — {scope_label}")
            cols = st.columns(len(collected) if collected else 1)
            for i, (g, count, rows) in enumerate(collected or []):
                cols[i].metric(f"{g.title()} titles", int(count))
            keep_meets_for_colors = ["Division I", "Division II", "Meet of Champions", "New Castle County", "Henlopen Conference", "Indoor State Championship"]
            scope_rows_list = []
            for g in genders:
                _, scoped_rows = title_count(df[df["gender"] == g], athlete, include_meets=include_meets, include_relays=False)
                if not scoped_rows.empty: scope_rows_list.append(scoped_rows)
            if scope_rows_list:
                scoped_df = pd.concat(scope_rows_list, ignore_index=True)
                chart_src = scoped_df.dropna(subset=["year", "meet"]).copy(); chart_src = chart_src[chart_src["meet"].isin(keep_meets_for_colors)]
                if not chart_src.empty:
                    stacked = (chart_src.groupby(["year","meet"]).size().reset_index(name="titles").sort_values(["year","meet"]))
                    chart = alt.Chart(stacked).mark_bar().encode(
                        x=alt.X("year:O", title="Year"), y=alt.Y("titles:Q", stack="zero", title="Titles"),
                        color=alt.Color("meet:N", legend=alt.Legend(title="Meet")),
                        tooltip=["year:O","meet:N","titles:Q"],
                    ).properties(title="Titles by year (stacked by meet)", width="container")
                    st.altair_chart(chart, use_container_width=True)
            if any(c for _, c, _ in collected):
                all_rows = pd.concat([r for _, c, r in collected if c > 0], ignore_index=True)
                if not all_rows.empty:
                    c1,c2,c3 = st.columns(3)
                    with c1: st.caption("By meet");  show_table(all_rows.groupby("meet").size().reset_index(name="titles"))
                    with c2: st.caption("By event"); show_table(all_rows.groupby("event").size().reset_index(name="titles"))
                    with c3: st.caption("By year");  show_table(all_rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
                    st.caption("Title rows (relays excluded)"); show_table(all_rows[["gender","year","meet","event","class","school","mark"]])
            else:
                st.info("No titles found for the selected scope.")

# ----------------------------
# MVPs
# ----------------------------
with tab4:
    st.subheader("MVPs — Indoor / Outdoor / Cross Country (from MVPs sheet)")
    if mvps_df is None or mvps_df.empty:
        st.info("No MVP data parsed.")
    else:
        c1, c2, c3 = st.columns(3)
        scope_pick = c1.selectbox("Scope", options=["Indoor", "Outdoor", "Cross Country"])
        gender_pick = c2.selectbox("Gender", options=["GIRLS", "BOYS"])
        since_year = c3.number_input("Since year (end year)", min_value=2000, max_value=2100, value=2010, step=1)
        cur = mvps_df[(mvps_df["scope"] == scope_pick) & (mvps_df["gender"] == gender_pick)]
        cur = cur[cur["season_end"] >= since_year].sort_values(["season_end"])
        if cur.empty:
            st.info("No MVP rows found for the current filters.")
        else:
            names = []
            for _, r in cur.iterrows():
                nm = str(r["name"]).strip()
                sch = str(r["school"]).strip() if pd.notna(r["school"]) and str(r["school"]).strip() else ""
                names.append(f"{nm}, {sch}" if sch else nm)
            st.caption("MVPs"); st.markdown("\n".join([f"- {s}" for s in sorted(set(names))]))
        show_table(cur[["gender","scope","season_label","name","school"]])

# ----------------------------
# Data Status
# ----------------------------
with tab5:
    st.subheader("Data status / debug")
    if df is None:
        st.info("No data loaded.")
    else:
        try:
            c1,c2,c3,c4,c5,c6 = st.columns(6)
            c1.metric("Champion rows", f"{len(df):,}")
            c2.metric("Min champ year", int(df["year"].min()))
            c3.metric("Max champ year", int(df["year"].max()))
            c4.metric("MVP rows", 0 if (mvps_df is None) else len(mvps_df))
            c5.metric("All‑time rows (Outdoor)", 0 if (alltime_df is None) else len(alltime_df))
            c6.metric("State‑record rows (Outdoor)", 0 if (state_records_df is None) else len(state_records_df))
        except Exception:
            st.metric("Champion rows", f"{len(df):,}")
        st.write("Meets (champions):", sorted(df["meet"].unique()))
        st.write("Events (sample):", sorted(df["event"].unique())[:16])
        if alltime_df is not None and not alltime_df.empty:
            st.write("All‑time events (Outdoor):", sorted(alltime_df["event"].dropna().unique().tolist())[:20])
        if state_records_df is not None and not state_records_df.empty:
            st.write("State‑record events (Outdoor):", sorted(state_records_df["event"].dropna().unique().tolist())[:20])
        if alltime_df_indoor is not None and not alltime_df_indoor.empty:
            st.write("All‑time events (Indoor):", sorted(alltime_df_indoor["event"].dropna().unique().tolist())[:20])
        if state_records_df_indoor is not None and not state_records_df_indoor.empty:
            st.write("State‑record events (Indoor):", sorted(state_records_df_indoor["event"].dropna().unique().tolist())[:20])
        st.write("Champions sample:"); show_table(df.head(20))

# ----------------------------
# State Records
# ----------------------------
with tab6:
    st.subheader("State Records")
    has_indoor = 'state_records_df_indoor' in globals() and state_records_df_indoor is not None and not state_records_df_indoor.empty
    season = st.radio("Season", options=( ["Outdoor", "Indoor"] if has_indoor else ["Outdoor"] ), horizontal=True, index=0)
    cur_df = state_records_df if season == "Outdoor" else state_records_df_indoor
    if cur_df is None or cur_df.empty:
        st.info(f"State records sheet not loaded or empty for {season}.")
    else:
        cur_df = cur_df.copy(); cur_df["event"] = cur_df["event"].astype(str).str.replace(r"\.0$", "", regex=True)
        events_sorted = sorted(cur_df["event"].dropna().unique().tolist())
        c1, c2 = st.columns([0.6, 0.4])
        ev_pick = c1.selectbox("Event", options=["(choose event)"] + events_sorted, index=0)
        g_pick = c2.radio("Gender", options=["Both", "Girls", "Boys"], horizontal=True, index=0)
        cur = cur_df.copy()
        if ev_pick != "(choose event)": cur = cur[cur["event"] == ev_pick]
        if g_pick == "Girls": cur = cur[cur["gender"] == "GIRLS"]
        elif g_pick == "Boys": cur = cur[cur["gender"] == "BOYS"]
        if ev_pick != "(choose event)":
            c_b, c_g = st.columns(2)
            boys = cur[cur["gender"] == "BOYS"].sort_values("event").head(1)
            girls = cur[cur["gender"] == "GIRLS"].sort_values("event").head(1)
            if not boys.empty:
                r = boys.iloc[0]
                with c_b:
                    with st.container(border=True):
                        st.markdown(f"**Boys — {r['event']} ({season})**")
                        st.markdown(f"- **Time/Mark:** {r['mark']}")
                        st.markdown(f"- **Athlete(s):** {r['name'] or r['school'] or ''}")
                        st.markdown(f"- **School:** {r['school'] or ''}")
                        st.markdown(f"- **Meet:** {r['meet'] or ''}")
                        st.markdown(f"- **Location:** {r['location'] or ''}")
                        st.markdown(f"- **Year:** {int(r['year']) if pd.notna(r['year']) else ''}")
            if not girls.empty:
                r = girls.iloc[0]
                with c_g:
                    with st.container(border=True):
                        st.markdown(f"**Girls — {r['event']} ({season})**")
                        st.markdown(f"- **Time/Mark:** {r['mark']}")
                        st.markdown(f"- **Athlete(s):** {r['name'] or r['school'] or ''}")
                        st.markdown(f"- **School:** {r['school'] or ''}")
                        st.markdown(f"- **Meet:** {r['meet'] or ''}")
                        st.markdown(f"- **Location:** {r['location'] or ''}")
                        st.markdown(f"- **Year:** {int(r['year']) if pd.notna(r['year']) else ''}")
            st.divider()
        st.caption(f"Full state records table — {season}"); show_table(cur[[c for c in ["gender","event","mark","name","school","meet","location","year"] if c in cur.columns]])

# ----------------------------
# All‑Time Lists
# ----------------------------
with tab7:
    st.subheader("All‑Time Lists (Top marks)")
    if alltime_df is None or alltime_df.empty:
        st.info("All‑time workbook not loaded. Add it to the repo to use this tab.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        g_pick   = c1.selectbox("Gender", options=sorted(alltime_df["gender"].dropna().unique().tolist()))
        evs_all  = sorted(alltime_df["event"].dropna().unique().tolist())
        ev_pick  = c2.selectbox("Event", options=evs_all)
        topn     = c3.number_input("Top N", min_value=1, max_value=100, value=20, step=1)
        yearflt  = c4.text_input("Year filter (e.g., 2018, 2018-2024; optional)")
        cur = alltime_df[(alltime_df["gender"] == g_pick) & (alltime_df["event"] == ev_pick)].copy()
        yf, yt = None, None
        if yearflt.strip():
            m = re.match(r"^\s*(20\d{2})\s*-\s*(20\d{2})\s*$", yearflt.strip())
            if m: yf, yt = int(m.group(1)), int(m.group(2))
            else:
                try: yf = yt = int(yearflt.strip())
                except Exception: pass
        if yf and yt: cur = cur[(cur["year"].astype("float") >= yf) & (cur["year"].astype("float") <= yt)]
        elif yf and not yt: cur = cur[cur["year"].astype("float") >= yf]
        cur = cur.sort_values("__sortkey", ascending=True).head(int(topn))
        st.metric("Entries shown", len(cur)); st.dataframe(cur[[c for c in ["rank","gender","event","mark","name","school","year","meet","location"] if c in cur.columns]], use_container_width=True, hide_index=True)
