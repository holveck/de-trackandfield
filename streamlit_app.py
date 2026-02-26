# streamlit_app.py
# ---------------------------------------------------------
# Delaware HS Track & Field — Natural-language Q&A + All-Time Lists
#
# Bundled data:
#   • Champions workbook: "Delaware Track and Field Supersheet (6).xlsx"
#   • All-time lists workbook: "Personal All-Time List.xlsx"  (per-event sheets)
#
# Highlights:
#   • NLQ intents: leaderboard, last-time, last-sweep, title-count, MVP lookup, records (all-time)
#   • Intent-specific presentation (cards, metrics, compact tables)
#   • Entity highlighting ("What I understood"), quick examples, clear-question button
#   • Meet/event canonicalization; "state" defaults; strict table cosmetics
#   • All-time loader parses per-event sheets (Girls/Boys <Event>), handles time & field marks
#
# Updates implemented:
#   • Removed intent headers (start results with info cards/tables)
#   • Added "Edit detected filters" expander (override parser outputs)
#   • Added Altair timeline for last-win / last-sweep
#   • Added synonym: "New Castle County championships"
#   • All-time tables now show Rank (first number column) + include Location
#   • Reduced quick example chips to four

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
        "indoor state championship", "indoor state championships"
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

# ---------- Event groups (requested definitions) ----------
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
    if choice: return MEET_CANONICAL[choice[0]]
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
            y = int(lbl.year); return y, y, str(y)
    except Exception:
        pass
    s = str(lbl).strip()
    m = re.match(r"^(20\d{2})\s*[-/]\s*(\d{2}|\d{4})$", s)
    if m:
        y1 = int(m.group(1)); y2txt = m.group(2)
        y2 = int(y2txt) if len(y2txt) == 4 else int(str(y1)[:2] + y2txt)
        return y1, y2, f"{y1}-{str(y2)[-2:]}"
    m2 = re.match(r"^(20\d{2})$", s)
    if m2:
        y = int(m2.group(1)); return y, y, s
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
        if not v: continue
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
    if txt is None: return None
    s = str(txt).strip()
    s = re.sub(r"[a-zA-Z]+$", "", s).strip()
    try:
        if ":" not in s:
            return float(s)
        parts = [p.strip() for p in s.split(":")]
        if len(parts) == 2:
            m, sec = parts; return 60.0*float(m) + float(sec)
        if len(parts) == 3:
            h, m, sec = parts; return 3600.0*float(h) + 60.0*float(m) + float(sec)
    except Exception:
        return None
    return None

def _parse_distance_to_float(txt: str) -> Optional[float]:
    if txt is None: return None
    s = str(txt).strip().lower()
    s = re.sub(r"[a-z]+$", "", s).strip()  # strip flags
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$", s)
    if m:
        feet = float(m.group(1)); inches = float(m.group(2))
        return feet*12.0 + inches  # inches
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

@st.cache_data(show_spinner=False)
def load_alltime(file_bytes: bytes, file_type: str = "xlsx") -> pd.DataFrame:
    """
    Load per-event all-time lists from a multi-sheet workbook.
    Sheet names: "Girls <Event>", "Boys <Event>"  (e.g., Girls 100, Boys Long Jump)
    Columns (flexible): Rank, (Time|Mark|Time/Mark), Name, School, Athletes (relays), Meet, Location, Date, Wind
    """
    assert file_type == "xlsx", "All-time loader expects .xlsx for the bundled workbook"
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
            # skip non per-event sheets (e.g., State records / log)
            continue

        gender_txt = m.group(1).upper()
        gender = "GIRLS" if gender_txt.startswith("GIRL") else "BOYS"
        event_raw = m.group(2).strip()
        event_norm_guess = re.sub(r"[,\s]+", " ", event_raw).strip()
        event_norm_guess = event_norm_guess.replace("1,600", "1600").replace("3,200", "3200").replace(",", "")
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

        # unify performance column
        perf_col = None
        for cand in ["time/mark","time","mark"]:
            if cand in df.columns:
                perf_col = cand; break
        if perf_col is None:
            perf_col = next((c for c in df.columns if c.replace(" ", "") in {"time","mark","time/mark"}), None)
        if perf_col is None:
            continue

        # rename to schema
        rename_map = {}
        for c in df.columns:
            lc = c.lower().strip()
            if lc in {"time/mark","time","mark"}: rename_map[c] = "mark"
            elif lc in {"name","athlete"}:        rename_map[c] = "name"
            elif lc == "school":                  rename_map[c] = "school"
            elif lc == "meet":                    rename_map[c] = "meet"
            elif lc == "date":                    rename_map[c] = "date"
            elif lc == "location":                rename_map[c] = "location"
            elif lc in {"wind reading","wind"}:   rename_map[c] = "wind"
            elif lc == "athletes":                rename_map[c] = "athletes"
            elif lc in {"rank", "#", "no", "number"}: rename_map[c] = "rank"
            else:
                rename_map[c] = c
        df = df.rename(columns=rename_map)

        # keep real rows only
        df = df[(df["mark"].notna()) & ((df.get("name").notna()) | (df.get("school").notna()))].copy()

        # derive year from date serials/strings
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

        # relays: if name missing but school present, set name to school label
        if "name" not in df.columns:
            df["name"] = None
        df.loc[df["name"].isna() & df.get("school").notna(), "name"] = df.loc[df["name"].isna() & df.get("school").notna(), "school"]

        # attach labels
        df["gender"] = gender
        df["event"]  = event_canonical

        # provenance + sort key
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
        tmp.groupby(["gender", "name", "name_norm"])["school"]
        .apply(lambda x: ", ".join(sorted({s for s in x.dropna()})))
        .reset_index(name="schools")
    )
    return schools.sort_values(["gender", "name"]).reset_index(drop=True)

def title_count(df: pd.DataFrame, athlete_name: str, *, include_meets: set, include_relays: bool = False) -> tuple[int, pd.DataFrame]:
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

def show_table(df: pd.DataFrame, cols: Optional[List[str]] = None):
    if df is None:
        return
    cur = df.copy()
    if cols:
        keep = [c for c in cols if c in cur.columns]
        cur = cur[keep]
    cur = _format_gender_values(cur)
    cur = _reorder_gender_first(cur)
    st.dataframe(cur, use_container_width=True, hide_index=True)

def show_alltime_table(df: pd.DataFrame, cols: Optional[List[str]] = None):
    """
    Dedicated table renderer for all-time results:
      - Rank (if present) as the first column
      - Include Location
      - Do NOT reorder gender to first (keep Rank at far left)
    """
    if df is None:
        return
    cur = df.copy()
    default_cols = []
    if "rank" in cur.columns: default_cols.append("rank")
    default_cols += ["gender","event","mark","name","school","year","meet"]
    if "location" in cur.columns: default_cols.append("location")
    if cols:
        for c in cols:
            if c in cur.columns and c not in default_cols:
                default_cols.append(c)
    keep = [c for c in default_cols if c in cur.columns]
    cur = cur[keep]
    cur = _format_gender_values(cur)
    st.dataframe(cur, use_container_width=True, hide_index=True)

def top1_card(name: str, school: str, gender: str, wins: int, context: str = ""):
    # Theme-aware card using native container border (no custom CSS needed)
    with st.container(border=True):
        st.markdown(f"**{name}** — {school}")
        parts = [f"Gender: **{gender.title()}**"]
        if context:
            parts.append(context)
        st.caption(" • ".join(parts))
        st.markdown(f"🏆 **{int(wins)}** wins")

def info_card(title: str, lines: list[tuple[str, str]]):
    with st.container(border=True):
        st.markdown(f"**{title}**")
        for k, v in lines:
            st.markdown(f"- **{k}:** {v}")

def metric_row(metrics: List[Tuple[str, str]]):
    cols = st.columns(len(metrics))
    for i, (label, value) in enumerate(metrics):
        cols[i].metric(label, value)

# ----------------------------
# NL helpers + multi-condition parsing & leaderboards
# ----------------------------
def canonical_event(event_text: str, gender: Optional[str]) -> Optional[str]:
    t = event_text.strip().lower()
    if t in EVENT_CANONICAL: return EVENT_CANONICAL[t]
    if "hurd" in t or t in {"55h", "110h", "100h"}:
        if gender == "GIRLS": return "100/55H"
        if gender == "BOYS":  return "110/55H"
        if "55" in t:         return "100/55H"
    if t in {"55", "55m"}: return "100/55"
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
            out |= EVENT_GROUPS[g]; continue
        ev = canonical_event(w, None)
        if ev: out.add(ev)
    return out

def _find_multi_targets(q: str, vocabulary: Dict[str, str]) -> List[str]:
    toks = _tokenize_phrases(q); found = []
    keys = sorted(vocabulary.keys(), key=len, reverse=True)
    for t in toks:
        for k in keys:
            if k in t:
                found.append(vocabulary[k]); break
    seen=set(); ordered=[]
    for x in found:
        if x not in seen:
            seen.add(x); ordered.append(x)
    return ordered

def _extract_year_range(q: str) -> Tuple[Optional[int], Optional[int]]:
    low = q.lower()
    m = re.search(r"(?:between|from)\s*(20\d{2})\s*(?:and|to|-)\s*(20\d{2})", low)
    if m: a, b = int(m.group(1)), int(m.group(2)); return (min(a,b), max(a,b))
    m = re.search(r"\b(since|after|from)\s*(20\d{2})\b", low)
    if m: return (int(m.group(2)), None)
    m = re.search(r"\bin\s*(20\d{2})\b", low)
    if m: return (int(m.group(1)), int(m.group(1)))
    yrs = [int(y) for y in re.findall(r"\b(20\d{2})\b", low)]
    if len(yrs) == 1: return (yrs[0], yrs[0])
    if len(yrs) >= 2: return (min(yrs[:2]), max(yrs[:2]))
    return (None, None)

def _extract_events_anywhere(q: str) -> set:
    low = q.lower(); events = set()
    for k in EVENT_CANONICAL.keys():
        if k in low: events.add(EVENT_CANONICAL[k])
    for m in re.findall(r"\b(55|100|200|400|800|1600|3200|lj|tj|hj|pv|300h|110h|100h)\b", low):
        ev = canonical_event(m, None)
        if ev: events.add(ev)
    for phrase in ["long jump","triple jump","high jump","pole vault","shot put","discus"]:
        if phrase in low: events.add(EVENT_CANONICAL[phrase])
    events |= _expand_event_groups(_tokenize_phrases(q))
    return events

def parse_question_multi(q: str) -> Dict[str, Optional[str]]:
    out = {
        "intent": None, "scope": None,
        "genders": [], "events": set(), "meets": [],
        "schools": [], "athletes": [],
        "year_from": None, "year_to": None,
        "raw": q, "top_n": 10, "track_only": False
    }
    low = q.lower()

    # Intents
    if re.search(r"\bhow many\b.*\b(championships?|titles?)\b", low):
        out["intent"] = "count_titles"
        if "state" in low:   out["scope"] = "state"
        if "indoor" in low:  out["scope"] = "indoor"
        if "outdoor" in low: out["scope"] = "outdoor"

    if "mvp" in low or "most valuable" in low:
        out["intent"] = "mvp_lookup"
        if "indoor" in low:          out["scope"] = "indoor"
        elif "outdoor" in low:       out["scope"] = "outdoor"
        elif "cross country" in low: out["scope"] = "cross country"

    # Leaderboard — both orders
    if (re.search(r"\b(most|record)\b.*\b(win|wins|won|titles?|races?)\b", low) or
        re.search(r"\b(win|wins|won|titles?|races?)\b.*\b(most|record)\b", low) or
        re.search(r"\btop\s+\d+\b", low)):
        out["intent"] = "leaderboard_wins"

    # Last time variants
    if re.search(r"\bwhen was the last time\b.*\bwon\b", low) or re.search(r"\bmost recent\b.*\bwin\b", low):
        out["intent"] = "last_win_time"
    if re.search(r"\bwho was the last\b.*\bto win\b", low):
        out["intent"] = "last_win_time"

    # Records / all-time lists
    if (re.search(r"\b(all[-\s]?time|top\s+times?|top\s+marks?|records?)\b", low) or
        re.search(r"\bfastest\b", low) or re.search(r"\bfurthest|longest\b", low) or
        re.search(r"\bbest\b.*\b(mark|time)\b", low)):
        out["intent"] = "records_lookup"

    # Top N
    m_top = re.search(r"\btop\s+(\d+)\b", low)
    if m_top:
        out["top_n"] = max(1, int(m_top.group(1)))
    if out["intent"] == "leaderboard_wins" and not m_top and re.search(r"\bwho\b", low) and re.search(r"\bmost\b", low):
        out["top_n"] = 1

    if "race" in low or "races" in low:
        out["track_only"] = True

    # Years
    yf, yt = _extract_year_range(q)
    out["year_from"], out["year_to"] = yf, yt

    # Genders
    g_tokens = []
    for tok in re.findall(r"[A-Za-z]+", q):
        lt = tok.lower()
        if lt in GENDER_CANONICAL:
            g_tokens.append(GENDER_CANONICAL[lt])
    out["genders"] = sorted(set(g_tokens))

    # Meets
    out["meets"] = _find_multi_targets(low, MEET_CANONICAL)

    # Events
    out["events"] = _extract_events_anywhere(q)

    # Schools (heuristic)
    for m in re.finditer(r"\b(?:from|at|by)\s+([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)+)", q):
        out["schools"].append(m.group(1).strip())

    # Athletes (quoted / lead-ins)
    for m in re.finditer(r"\"([^\"]+)\"", q):
        out["athletes"].append(m.group(1).strip())
    m = re.search(r"\b(?:has|did|for|by|from|at)\s+([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)+)", q)
    if m:
        out["athletes"].append(m.group(1).strip())
   m = re.search(r"\bby\s+([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)+)", q)

    out["schools"]  = list(dict.fromkeys(out["schools"]))
    out["athletes"] = list(dict.fromkeys(out["athletes"]))

    # SAME-MEET SWEEP trigger
    if len(out["events"]) >= 2:
        if (out["intent"] in ("last_win_time", "last_sweep") or
            re.search(r"\b(last|most recent)\b", low) or
            re.search(r"\bto win\b", low)):
            out["intent"] = "last_sweep"

    return out

def apply_multi_filters(df: pd.DataFrame, f: Dict[str, Optional[str]]) -> pd.DataFrame:
    cur = df.copy()
    if f["genders"]:
        cur = cur[cur["gender"].isin(set(f["genders"]))]
    if f["meets"]:
        cur = cur[cur["meet"].isin(set(f["meets"]))]
    if f["events"]:
        cur = cur[cur["event"].isin(set(f["events"]))]
    if f["schools"]:
        mask = pd.Series(False, index=cur.index)
        for s in f["schools"]:
            needle = s.lower()
            mask = mask | cur["school"].str.lower().str.contains(needle, na=False)
        cur = cur[mask]
    if f["athletes"]:
        mask = pd.Series(False, index=cur.index)
        for a in f["athletes"]:
            needle = a.lower()
            mask = mask | cur["name"].str.lower().str.contains(needle, na=False)
        cur = cur[mask]
    yf, yt = f.get("year_from"), f.get("year_to")
    if yf and yt:
        cur = cur[(cur["year"] >= yf) & (cur["year"] <= yt)]
    elif yf and not yt:
        cur = cur[cur["year"] >= yf]
    if f.get("track_only"):
        cur = cur[cur["event"].isin(TRACK_EVENTS)]
    return cur.sort_values(["gender","event","meet","year"], ascending=[True,True,True,False])

def leaderboard_wins(df: pd.DataFrame, f: Dict[str, Optional[str]]) -> pd.DataFrame:
    cur = apply_multi_filters(df, f)
    grp = (cur.groupby(["gender","name","school"])
              .size()
              .reset_index(name="wins")
              .sort_values(["wins","gender","name"], ascending=[False, True, True]))
    top_n = f.get("top_n", 10)
    return grp.head(top_n)

# ----------------------------
# Timeline helper
# ----------------------------
def plot_timeline_year_counts(df: pd.DataFrame, *, title: str, year_col: str = "year"):
    chart_df = (
        df.dropna(subset=[year_col])
          .groupby(year_col).size()
          .reset_index(name="count")
          .sort_values(year_col)
    )
    if chart_df.empty:
        return None
    c = alt.Chart(chart_df).mark_bar().encode(
        x=alt.X(f"{year_col}:O", title="Year"),
        y=alt.Y("count:Q", title="Count"),
        tooltip=[alt.Tooltip(f"{year_col}:O", title="Year"), alt.Tooltip("count:Q", title="Count")]
    ).properties(title=title, width="container")
    return c

# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="DE HS Track & Field — Q&A + All-Time", page_icon="🏃", layout="wide")
st.title("Delaware HS Track & Field — Q&A + All‑Time")

# ---------- Bundled, path-safe loader ----------
APP_DIR = Path(__file__).parent

BUNDLED_XLSX_NAME = "Delaware Track and Field Supersheet (6).xlsx"
BUNDLED_XLSX_PATH = APP_DIR / BUNDLED_XLSX_NAME

# All-time bundled workbook (from your repo)
BUNDLED_ALLTIME_XLSX_NAME = "Personal All-Time List.xlsx"
BUNDLED_ALLTIME_XLSX_PATH = APP_DIR / BUNDLED_ALLTIME_XLSX_NAME

with st.sidebar:
    st.header("Data source")
    if not BUNDLED_XLSX_PATH.exists():
        st.error(
            f"Bundled champions workbook not found at:\n{BUNDLED_XLSX_PATH}\n\n"
            "• Ensure the file is in the repo alongside streamlit_app.py\n"
            "• Verify the name matches exactly."
        )
        df = None; mvps_df = None; KNOWN_SCHOOLS = set(); KNOWN_ATHLETES = set()
    else:
        try:
            with open(BUNDLED_XLSX_PATH, "rb") as f:
                file_bytes = f.read()
            df = load_champions(file_bytes)
            mvps_df = load_mvps(file_bytes)
            KNOWN_SCHOOLS  = {s for s in df["school"].dropna().unique()}
            KNOWN_ATHLETES = {n for n in df["name"].dropna().unique()}
            st.success(f"Loaded champions: {len(df):,} rows")
            st.success(f"Loaded MVPs: {0 if mvps_df is None else len(mvps_df):,} rows")
            st.caption(f"Champions workbook: {BUNDLED_XLSX_NAME}")
        except Exception as ex:
            st.error("There was a problem parsing the bundled champions workbook.")
            st.exception(ex)
            df = None; mvps_df = None; KNOWN_SCHOOLS = set(); KNOWN_ATHLETES = set()

    st.header("All‑Time Lists (bundled)")
    alltime_df = None
    if not BUNDLED_ALLTIME_XLSX_PATH.exists():
        st.warning(
            f"All‑time workbook not found at:\n{BUNDLED_ALLTIME_XLSX_PATH}\n\n"
            "• Add the file to the repo to enable all‑time/records features."
        )
    else:
        try:
            with open(BUNDLED_ALLTIME_XLSX_PATH, "rb") as f:
                at_bytes = f.read()
            alltime_df = load_alltime(at_bytes, "xlsx")
            st.success(f"Loaded all‑time rows: {len(alltime_df):,}")
            st.caption(f"All‑time workbook: {BUNDLED_ALLTIME_XLSX_NAME}")
        except Exception as ex:
            st.error("There was a problem parsing the bundled all‑time workbook.")
            st.exception(ex)
            alltime_df = None

# ------------------------------------------------

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🔎 Ask a question", "🎛️ Explore", "👤 Athlete profiles",
    "🏆 MVPs", "🛠️ Data status", "🥇 All‑Time Lists"
])

# ----------------------------
# Q&A
# ----------------------------
with tab1:
    st.subheader("Natural-language Q&A")
    st.caption(
        "Ask questions about state champions, leaderboards, MVPs, sweeps, and all‑time lists."
    )

    # Deep-linking helpers
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

    # Prime from URL if empty
    if "q_prefill" not in st.session_state or not st.session_state["q_prefill"]:
        url_q = _get_q_from_url()
        if url_q:
            st.session_state["q_prefill"] = url_q

    # Quick example chips (reduced to four)
    example_prompts = [
        "Who won the boys 200 at the indoor state meet in 2026?",
        "How many state championships did Juliana Balon win?",
        "Who has won the most New Castle County Championships races?",
        "Who was the last girl to win the 55, 55H and 200 at the indoor track and field state meet?"
    ]
    st.caption("Quick examples:")
    ex_cols = st.columns(2)
    for i, ex in enumerate(example_prompts):
        if ex_cols[i % 2].button(ex):
            st.session_state["q_prefill"] = ex
            _set_q_in_url(ex)
            st.rerun()

    # Question bar + Clear
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

    # "What I understood" line
    def _render_understood(fm: Dict[str, Optional[str]]):
        parts = []
        if fm.get("genders"):
            parts.append("**Gender:** " + ", ".join([g.title() for g in fm["genders"]]))
        if fm.get("events"):
            parts.append("**Events:** " + ", ".join(sorted(fm["events"])))
        if fm.get("meets"):
            parts.append("**Meets:** " + ", ".join(fm["meets"]))
        if fm.get("schools"):
            parts.append("**Schools:** " + ", ".join(fm["schools"]))
        if fm.get("athletes"):
            parts.append("**Athletes:** " + ", ".join(fm["athletes"]))
        yf, yt = fm.get("year_from"), fm.get("year_to")
        if yf and yt and yf == yt:
            parts.append(f"**Year:** {yf}")
        elif yf and yt:
            parts.append(f"**Years:** {yf}–{yt}")
        elif yf and not yt:
            parts.append(f"**Since:** {yf}")
        intent = fm.get("intent")
        if intent:
            label = {
                "leaderboard_wins": "Leaderboard",
                "count_titles": "Title Count",
                "mvp_lookup": "MVP Lookup",
                "last_win_time": "Last Time",
                "last_sweep": "Last Sweep",
                "records_lookup": "All‑Time / Records",
            }.get(intent, intent)
            parts.insert(0, f"**Intent:** {label}")
        if parts:
            st.markdown("🧠 *What I understood:* " + " — ".join(parts))

    # Filter override UI
    def _edit_detected_filters_ui(fm: dict, *, df: pd.DataFrame, alltime_df: Optional[pd.DataFrame]):
        with st.expander("Edit detected filters", expanded=False):
            genders_opts = ["GIRLS", "BOYS"]
            meets_opts   = sorted(df["meet"].dropna().unique().tolist())
            events_opts  = sorted(df["event"].dropna().unique().tolist())

            sel_genders = st.multiselect("Gender", options=genders_opts, default=fm.get("genders", []))
            sel_meets   = st.multiselect("Meets", options=meets_opts,  default=fm.get("meets", []))
            sel_events  = st.multiselect("Events", options=events_opts, default=sorted(fm.get("events", [])))
            school_txt  = st.text_input("School contains (optional)", value="; ".join(fm.get("schools", [])))
            athlete_txt = st.text_input("Athlete contains (optional)", value="; ".join(fm.get("athletes", [])))

            c1, c2 = st.columns(2)
            yf_default = int(fm["year_from"]) if fm.get("year_from") else 2000
            yt_default = int(fm["year_to"]) if fm.get("year_to") else 2100
            yf = c1.number_input("Year from (optional)", min_value=1900, max_value=2100, value=yf_default, step=1, key="edit_yf")
            yt = c2.number_input("Year to (optional)",   min_value=1900, max_value=2100, value=yt_default, step=1, key="edit_yt")

            if st.button("Apply filter overrides"):
                fm["genders"]  = sel_genders
                fm["meets"]    = sel_meets
                fm["events"]   = set(sel_events)
                fm["schools"]  = [s.strip() for s in school_txt.split(";") if s.strip()]
                fm["athletes"] = [s.strip() for s in athlete_txt.split(";") if s.strip()]
                fm["year_from"] = int(yf) if yf != 2000 else fm.get("year_from")
                fm["year_to"]   = int(yt) if yt != 2100 else fm.get("year_to")
        return fm

    if q and df is not None:
        f_multi = parse_question_multi(q)

        def _apply_state_default(fm: Dict[str, Optional[str]], text: str):
            lowx = text.lower()
            if ("state" in lowx) and ("indoor" not in lowx) and ("outdoor" not in lowx) and not fm["meets"]:
                fm["meets"] = list(STATE_MEETS_ALL)

        def _auto_add_schools(fm: Dict[str, Optional[str]]):
            if not fm["schools"] and 'KNOWN_SCHOOLS' in globals() and KNOWN_SCHOOLS:
                lowq = fm["raw"].lower()
                for s in KNOWN_SCHOOLS:
                    if isinstance(s, str) and s.lower() in lowq:
                        fm["schools"].append(s)

        # Show understood entities before results
        _render_understood(f_multi)
        # Provide override UI
        f_multi = _edit_detected_filters_ui(f_multi, df=df, alltime_df=alltime_df)

        # ---- Title count ----
        if f_multi.get("intent") == "count_titles":
            if not f_multi["athletes"]:
                lowp = f_multi["raw"].lower()
                candidates = []
                for n in KNOWN_ATHLETES:
                    if isinstance(n, str) and n.lower() in lowp:
                        candidates.append(n)
                if candidates:
                    f_multi["athletes"] = sorted(set(candidates), key=lambda s: (-len(s), s))
            if not f_multi["athletes"]:
                st.info('I couldn’t identify the athlete’s name. Try quoting it, e.g., “How many state championships has "Juliana Balon" won?”')
                st.stop()

            scope = f_multi.get("scope")
            if scope == "indoor":
                include_meets = STATE_MEETS_INDOOR; scope_label = "Indoor state championships"
            elif scope == "outdoor":
                include_meets = STATE_MEETS_OUTDOOR; scope_label = "Outdoor state championships"
            else:
                include_meets = STATE_MEETS_ALL if ("state" in (f_multi.get("scope") or "") or "state" in q.lower()) else set(df["meet"].unique())
                scope_label = "State championships" if include_meets == STATE_MEETS_ALL else "Championships (all meets)"

            athlete = f_multi["athletes"][0]
            if f_multi["genders"]:
                df_scope = df[df["gender"].isin(f_multi["genders"])]
                total_count, rows = title_count(df_scope, athlete, include_meets=include_meets, include_relays=False)
                metric_row([("Titles", str(total_count))])
                if total_count > 0:
                    c1,c2,c3 = st.columns(3)
                    with c1: st.caption("By meet");  show_table(rows.groupby("meet").size().reset_index(name="titles"))
                    with c2: st.caption("By event"); show_table(rows.groupby("event").size().reset_index(name="titles"))
                    with c3: st.caption("By year");  show_table(rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
                    st.caption("Title rows (relays excluded)")
                    show_table(rows[["gender","year","meet","event","class","school","mark"]])
                st.stop()
            else:
                genders = guess_gender_for_name(df, athlete)
                metric_row([("Possible genders", " / ".join([g.title() for g in genders]))])
                for g in genders:
                    c, rws = title_count(df[df["gender"] == g], athlete, include_meets=include_meets, include_relays=False)
                    st.caption(f"{g.title()} titles: {c}")
                    if c > 0:
                        show_table(rws[["gender","year","meet","event","class","school","mark"]])
                st.stop()

        # ---- MVP lookup ----
        if f_multi.get("intent") == "mvp_lookup" and mvps_df is not None:
            scope_map = {"indoor": "Indoor", "outdoor": "Outdoor", "cross country": "Cross Country"}
            mvps_scope = scope_map.get(f_multi.get("scope"), None)
            cur = mvps_df.copy()
            if mvps_scope: cur = cur[cur["scope"] == mvps_scope]
            if f_multi["genders"]: cur = cur[cur["gender"].isin(f_multi["genders"])]

            if f_multi["schools"]:
                mask = pd.Series(False, index=cur.index)
                for s in f_multi["schools"]:
                    needle = s.lower()
                    mask = mask | cur["school"].str.lower().str.contains(needle, na=False)
                cur = cur[mask]
            else:
                lowp = f_multi["raw"].lower()
                auto = []
                for s in KNOWN_SCHOOLS:
                    if isinstance(s,str) and s.lower() in lowp:
                        auto.append(s)
                if auto:
                    mask = pd.Series(False, index=cur.index)
                    for s in auto:
                        needle = s.lower()
                        mask = mask | cur["school"].str.lower().str.contains(needle, na=False)
                    cur = cur[mask]

            yf, yt = f_multi["year_from"], f_multi["year_to"]
            if yf and yt:
                cur = cur[(cur["season_end"] >= yf) & (cur["season_start"] <= yt)]
            elif yf and not yt:
                cur = cur[cur["season_end"] >= yf]

            st.metric("MVP entries", len(cur))
            if cur.empty:
                st.warning("No MVPs matched your filters.")
            else:
                show_table(cur.sort_values(["season_end","gender","scope"])[["gender","scope","season_label","name","school","category"]])
            st.stop()

        # ---- Last time (single event) ----
        if f_multi.get("intent") == "last_win_time":
            lowp = f_multi["raw"].lower()
            if not f_multi["meets"]:
                if "indoor" in lowp:
                    f_multi["meets"] = list(STATE_MEETS_INDOOR)
                elif "outdoor" in lowp:
                    f_multi["meets"] = list(STATE_MEETS_OUTDOOR)
                elif "state" in lowp:
                    f_multi["meets"] = list(STATE_MEETS_ALL)
            _auto_add_schools(f_multi)

            cur = apply_multi_filters(df, f_multi)
            cur = cur[~cur["event"].isin(EVENT_GROUPS["relays"])]

            if cur.empty:
                st.error("I couldn't find a matching winner. Try specifying gender/event/meet more precisely.")
                with st.expander("Detected filters"): st.json(f_multi)
                st.stop()

            latest_year = int(cur["year"].max())
            latest_rows = cur[cur["year"] == latest_year].sort_values(["gender","meet","event"])
            r0 = latest_rows.iloc[0]
            info_card(
                title=f"{str(r0['gender']).title()} — {r0['event']} — {r0['meet']}",
                lines=[
                    ("Year", str(latest_year)),
                    ("Athlete", str(r0["name"])),
                    ("Time/Mark", str(r0["mark"])),
                    ("School", str(r0["school"])),
                ],
            )
            # Timeline of wins over the years (matching filters)
            timeline_src = cur.copy()
            timeline_src = timeline_src[~timeline_src["event"].isin(EVENT_GROUPS["relays"])]
            timeline_chart = plot_timeline_year_counts(timeline_src, title="Wins by Year (matching your filters)")
            if timeline_chart is not None:
                st.altair_chart(timeline_chart, use_container_width=True)

            if len(latest_rows) > 1:
                st.caption("All matching winners in that year")
                show_table(latest_rows[["gender","year","meet","event","name","school","class","mark"]])
            st.stop()

        # ---- Last sweep (multi-event, same meet & year) ----
        if f_multi.get("intent") == "last_sweep":
            lowp = f_multi["raw"].lower()
            if not f_multi["meets"]:
                if "indoor" in lowp:
                    f_multi["meets"] = list(STATE_MEETS_INDOOR)
                elif "outdoor" in lowp:
                    f_multi["meets"] = list(STATE_MEETS_OUTDOOR)
                elif "state" in lowp:
                    f_multi["meets"] = list(STATE_MEETS_ALL)

            required_events = set(f_multi["events"])
            if len(required_events) < 2:
                st.info("Please specify at least two events for a sweep check (e.g., 800, 1600 and 3200).")
                st.stop()

            cur = df.copy()
            if f_multi["genders"]:
                cur = cur[cur["gender"].isin(f_multi["genders"])]
            if f_multi["meets"]:
                cur = cur[cur["meet"].isin(f_multi["meets"])]
            cur = cur[cur["event"].isin(required_events)]
            cur = cur[~cur["event"].isin(EVENT_GROUPS["relays"])]

            if cur.empty:
                st.error("No matches for the specified sweep filters.")
            else:
                agg = (cur.groupby(["year","meet","gender","name"])["event"]
                          .apply(set)
                          .reset_index(name="events_won"))
                agg["has_all"] = agg["events_won"].apply(lambda s: required_events.issubset(s))
                sweeps = agg[agg["has_all"]]

                if sweeps.empty:
                    st.warning("No athlete found who swept all those events at a single meet.")
                    with st.expander("Detected filters"): st.json(f_multi)
                    st.stop()

                last_year = int(sweeps["year"].max())
                last_hits = sweeps[sweeps["year"] == last_year].sort_values(["gender","meet","name"])
                winners = ", ".join(sorted(last_hits["name"].unique()))
                metric_row([("Year", str(last_year)), ("Winner(s)", winners)])

                # Timeline for sweep years
                sweep_years_df = sweeps[["year"]].copy()
                timeline_chart = plot_timeline_year_counts(sweep_years_df.rename(columns={"year":"year"}), title="Years with a full sweep")
                if timeline_chart is not None:
                    st.altair_chart(timeline_chart, use_container_width=True)

                detail = cur[cur["year"] == last_year]
                detail = detail.merge(last_hits[["year","meet","gender","name"]], on=["year","meet","gender","name"], how="inner")
                detail = detail.sort_values(["gender","meet","name","event"])
                show_table(detail[["gender","year","meet","name","event","school","class","mark"]])
            st.stop()

        # ---- Leaderboard (who has won the most...) ----
        if f_multi.get("intent") == "leaderboard_wins":
            _apply_state_default(f_multi, f_multi["raw"])
            _auto_add_schools(f_multi)

            lb = leaderboard_wins(df, f_multi)

            if f_multi.get("top_n", 10) == 1 and not lb.empty:
                row = lb.iloc[0]
                bits = []
                if f_multi["genders"]: bits.append("/".join([g.title() for g in f_multi["genders"]]))
                if f_multi["events"]:  bits.append(", ".join(sorted(f_multi["events"])))
                if f_multi["meets"]:   bits.append(", ".join(f_multi["meets"]))
                context = " — ".join([b for b in bits if b])
                top1_card(name=row["name"], school=row["school"], gender=row["gender"], wins=int(row["wins"]), context=context)

                cur = apply_multi_filters(df, f_multi)
                wins_rows = cur[
                    (cur["name"] == row["name"]) &
                    (cur["school"] == row["school"]) &
                    (cur["gender"] == row["gender"])
                ].sort_values(["year","meet","event"], ascending=[False, True, True])
                if not wins_rows.empty:
                    st.caption("That athlete’s wins (matching your filters)")
                    show_table(wins_rows[["gender","year","meet","event","class","school","mark"]])
                st.stop()

            if lb.empty:
                st.error("No matching winners found for your leaderboard filters.")
                with st.expander("Detected filters"): st.json(f_multi)
            else:
                show_table(lb.reset_index(drop=True)[["gender","name","school","wins"]])
            st.stop()

        # ---- Records / All-time lists ----
        if f_multi.get("intent") == "records_lookup":
            if alltime_df is None or alltime_df.empty:
                st.error("All‑time workbook not loaded. Add it to the repo to use records queries.")
                with st.expander("Detected filters"): st.json(f_multi)
                st.stop()

            genders = f_multi.get("genders") or sorted(alltime_df["gender"].dropna().unique().tolist())
            events  = sorted(f_multi.get("events") or [])
            meets   = f_multi.get("meets") or []
            yf, yt  = f_multi.get("year_from"), f_multi.get("year_to")

            if not events:
                st.info("Please specify at least one event for all‑time records, e.g., “girls 400 all‑time”.")
                st.stop()

            cur = alltime_df.copy()
            cur = cur[cur["gender"].isin(genders)]
            cur = cur[cur["event"].isin(events)]
            if meets:
                cur = cur[cur["meet"].isin(meets)]
            if yf and yt:
                cur = cur[(cur["year"].astype("float") >= yf) & (cur["year"].astype("float") <= yt)]
            elif yf and not yt:
                cur = cur[cur["year"].astype("float") >= yf]

            if cur.empty:
                st.error("No all‑time entries matched your filters.")
                with st.expander("Detected filters"): st.json(f_multi)
                st.stop()

            topn = 1 if (re.search(r"\bwho\b", f_multi["raw"].lower()) and len(events) == 1) else 10
            m_top = re.search(r"\btop\s+(\d+)\b", f_multi["raw"].lower())
            if m_top:
                topn = max(1, int(m_top.group(1)))

            outs = []
            for g in genders:
                for ev in events:
                    chunk = cur[(cur["gender"] == g) & (cur["event"] == ev)].sort_values("__sortkey", ascending=True).head(topn)
                    if not chunk.empty:
                        outs.append(chunk)
            if not outs:
                st.error("No all‑time results to display.")
                st.stop()

            res = pd.concat(outs, ignore_index=True)

            if len(genders) == 1 and len(events) == 1 and topn == 1 and len(res) >= 1:
                r0 = res.iloc[0]
                info_card(
                    title=f"{str(r0['gender']).title()} — {r0['event']} (All‑time best)",
                    lines=[
                        ("Athlete/Team", str(r0["name"])),
                        ("Time/Mark", str(r0["mark"])),
                        ("School", str(r0["school"] or "")),
                        ("Year", str(r0["year"] or "")),
                        ("Meet", str(r0["meet"] or "")),
                    ],
                )
                st.stop()

            # All-time list view with Rank + Location
            show_alltime_table(res, cols=["location"])
            st.stop()

        # ---- Fallback ----
        _apply_state_default(f_multi, f_multi["raw"])
        if ({"100/55","100/55H","110/55H"} & f_multi["events"]) and not f_multi["meets"]:
            f_multi["meets"] = list(STATE_MEETS_INDOOR)
        _auto_add_schools(f_multi)

        result = apply_multi_filters(df, f_multi)

        if result.empty:
            st.error("No matches found. Try adjusting events/meets/schools/years.")
            with st.expander("Detected filters"): st.json(f_multi)
        else:
            if (f_multi.get("year_from") == f_multi.get("year_to")
                and len(f_multi.get("events", [])) == 1
                and len(result) == 1):
                row = result.iloc[0]
                info_card(
                    title=f"{str(row['gender']).title()} {row['event']} — {row['meet']} {row['year']}",
                    lines=[
                        ("Winner", str(row["name"])),
                        ("Class", str(row.get("class", ""))),
                        ("School", str(row["school"])),
                        ("Mark", str(row["mark"])),
                    ],
                )
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
            cur = cur[
                cur["name"].str.lower().str.contains(needle, na=False) |
                cur["school"].str.lower().str.contains(needle, na=False)
            ]
        st.metric("Matching champions", f"{len(cur):,}")
        show_table(
            cur.sort_values(["gender","event","meet","year"], ascending=[True,True,True,False]).reset_index(drop=True)
        )

# ----------------------------
# Athlete Profiles (relays excluded)
# ----------------------------
with tab3:
    st.subheader("Athlete profiles (relays excluded)")
    if df is None:
        st.info("No data loaded.")
    else:
        idx = all_athletes_index(df)
        athlete = st.selectbox("Choose athlete", options=["(type to search)"] + idx["name"].unique().tolist(), index=0)
        scope = st.radio("Scope", options=["State (Indoor + Division I/II)", "Indoor only", "Outdoor only", "All meets"], horizontal=True)

        if athlete and athlete != "(type to search)":
            if scope == "Indoor only":
                include_meets = STATE_MEETS_INDOOR; scope_label = "Indoor State Championship"
            elif scope == "Outdoor only":
                include_meets = STATE_MEETS_OUTDOOR; scope_label = "Outdoor (Division I & II)"
            elif scope == "All meets":
                include_meets = set(df["meet"].unique()); scope_label = "All meets"
            else:
                include_meets = STATE_MEETS_ALL; scope_label = "State (Indoor + Division I/II)"

            genders = guess_gender_for_name(df, athlete)
            collected = []
            for g in genders:
                count, rows = title_count(df[df["gender"] == g], athlete, include_meets=include_meets, include_relays=False)
                collected.append((g, count, rows))

            st.markdown(f"### {athlete} — {scope_label}")
            cols = st.columns(len(collected) if collected else 1)
            for i, (g, count, rows) in enumerate(collected or []):
                cols[i].metric(f"{g.title()} titles", int(count))

            if any(c for _, c, _ in collected):
                all_rows = pd.concat([r for _, c, r in collected if c > 0], ignore_index=True)
                if not all_rows.empty:
                    c1,c2,c3 = st.columns(3)
                    with c1: st.caption("By meet");  show_table(all_rows.groupby("meet").size().reset_index(name="titles"))
                    with c2: st.caption("By event"); show_table(all_rows.groupby("event").size().reset_index(name="titles"))
                    with c3: st.caption("By year");  show_table(all_rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
                    st.caption("Title rows (relays excluded)")
                    show_table(all_rows[["gender","year","meet","event","class","school","mark"]])
            else:
                st.info("No titles found for the selected scope.")

# ----------------------------
# MVPs
# ----------------------------
with tab4:
    st.subheader("MVPs — Indoor / Outdoor / Cross Country (from MVPs sheet)")
    if mvps_df is None or mvps_df.empty:
        st.info("No MVP data parsed. Ensure the 'MVPs' sheet exists and follows the expected layout (school next column; co‑MVPs within a season).")
    else:
        c1, c2, c3 = st.columns(3)
        scope_pick = c1.selectbox("Scope", options=["Indoor", "Outdoor", "Cross Country"])
        gender_pick = c2.selectbox("Gender", options=["GIRLS", "BOYS"])
        since_year = c3.number_input("Since year (end year)", min_value=2000, max_value=2100, value=2010, step=1)

        cur = mvps_df[(mvps_df["scope"] == scope_pick) & (mvps_df["gender"] == gender_pick)]
        cur = cur[cur["season_end"] >= since_year].sort_values(["season_end"])
        st.metric("MVP entries", len(cur))
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
            c1,c2,c3,c4,c5 = st.columns(5)
            c1.metric("Champion rows", f"{len(df):,}")
            c2.metric("Min champ year", int(df["year"].min()))
            c3.metric("Max champ year", int(df["year"].max()))
            c4.metric("MVP rows", 0 if (mvps_df is None) else len(mvps_df))
            c5.metric("All‑time rows", 0 if (alltime_df is None) else len(alltime_df))
        except Exception:
            st.metric("Champion rows", f"{len(df):,}")
        st.write("Meets (champions):", sorted(df["meet"].unique()))
        st.write("Events (sample):", sorted(df["event"].unique())[:16])
        if alltime_df is not None and not alltime_df.empty:
            st.write("All‑time events:", sorted(alltime_df["event"].dropna().unique().tolist())[:20])
        if mvps_df is not None and not mvps_df.empty:
            st.write("MVP scopes:", sorted(mvps_df["scope"].unique().tolist()))
            st.write("MVP seasons range:", f"{int(mvps_df['season_end'].min())} → {int(mvps_df['season_end'].max())}")
        st.write("Champions sample:")
        show_table(df.head(20))

# ----------------------------
# All‑Time Lists
# ----------------------------
with tab6:
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
            if m:
                yf, yt = int(m.group(1)), int(m.group(2))
            else:
                try:
                    yf = yt = int(yearflt.strip())
                except Exception:
                    pass
        if yf and yt:
            cur = cur[(cur["year"].astype("float") >= yf) & (cur["year"].astype("float") <= yt)]
        elif yf and not yt:
            cur = cur[cur["year"].astype("float") >= yf]

        cur = cur.sort_values("__sortkey", ascending=True).head(int(topn))
        st.metric("Entries shown", len(cur))
        show_alltime_table(cur, cols=["location"])
