# streamlit_app.py
# ---------------------------------------------------------
# Delaware HS Track & Field Champions Q&A — Streamlit
# Bundled workbook build (no upload required).
#
# Features:
#   • Bundled, path-safe loader for the Excel workbook
#   • Parses GIRLS + BOYS champions into a normalized table
#   • Natural-language Q&A + Title-count intent
#   • Athlete Profiles (relays excluded)
#   • MVPs parsing + Q&A (school to the right; co‑MVPs in same season block)
#   • Multi-condition queries (events/meets/schools/athletes/genders + year ranges)
#   • Leaderboards: “Who has won the most …”
#   • “State” rule: if prompt contains 'state' but NOT 'indoor'/'outdoor',
#       default meets → Division I, Division II, Indoor State Championship
#   • MVPs tab + Data Status tab

import io
import re
import difflib
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import pandas as pd
import streamlit as st
import openpyxl

# ----------------------------
# Canonical dictionaries & aliases
# ----------------------------

MEETS: Dict[str, set] = {
    "Division I": {"division 1", "div i", "d1"},
    "Division II": {"division 2", "div ii", "d2"},
    "Meet of Champions": {"moc", "meet of champs", "meet of champion"},
    "New Castle County": {"ncc", "new castle", "new castle co"},
    "Henlopen Conference": {"henlopen"},
    "Indoor State Championship": {"indoor", "indoor state", "state indoor", "indoor championship"},
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
    "sprints": {"100/55", "200", "400"},
    "distance": {"800", "1600", "3200"},
    "hurdles": {"100/55H", "110/55H", "300H"},
    "jumps": {"LJ", "TJ", "HJ", "PV"},
    "throws": {"Shot put", "Discus"},
    "relays": {"4x100", "4x200", "4x400", "4x800"},
}
EVENT_GROUP_SYNONYMS = {
    "sprint": "sprints", "sprinters": "sprints", "sprints": "sprints",
    "distance": "distance", "distances": "distance",
    "hurdle": "hurdles", "hurdles": "hurdles", "hurdling": "hurdles",
    "jump": "jumps", "jumps": "jumps", "field jumps": "jumps",
    "throw": "throws", "throws": "throws",
    "relay": "relays", "relays": "relays", "4x": "relays",
}

# Track vs field partition (for “races”)
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
        if current_event and isinstance(meet_raw, str) and meet_raw.strip() in MEETS:
            meet_name = meet_raw.strip()
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
# MVPs parsing (school to right; co‑MVPs)
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
        if isinstance(lbl, _dt.datetime) or isinstance(lbl, _dt.date):
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
# NL helpers + multi-condition parsing & leaderboards
# ----------------------------
def canonical_meet(token: str) -> Optional[str]:
    low = token.strip().lower()
    if low in MEET_CANONICAL:
        return MEET_CANONICAL[low]
    choice = difflib.get_close_matches(low, list(MEET_CANONICAL.keys()), n=1, cutoff=0.85)
    if choice: return MEET_CANONICAL[choice[0]]
    return None

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
    # explicit canonical keys
    for k in EVENT_CANONICAL.keys():
        if k in low: events.add(EVENT_CANONICAL[k])
    # numeric/short tokens
    for m in re.findall(r"\b(55|100|200|400|800|1600|3200|lj|tj|hj|pv|300h|110h|100h)\b", low):
        ev = canonical_event(m, None)
        if ev: events.add(ev)
    # long phrases for field events
    for phrase in ["long jump","triple jump","high jump","pole vault","shot put","discus"]:
        if phrase in low: events.add(EVENT_CANONICAL[phrase])
    # groups anywhere, and OR lists
    for syn, grp in EVENT_GROUP_SYNONYMS.items():
        if syn in low: events |= EVENT_GROUPS[grp]
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

    # intents
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
    # leaderboard intent
    if re.search(r"\bmost\b.*\b(win|wins|titles|races)\b", low) or re.search(r"\btop\s+\d+\b", low):
        out["intent"] = "leaderboard_wins"
    m_top = re.search(r"\btop\s+(\d+)\b", low)
    if m_top: out["top_n"] = max(1, int(m_top.group(1)))
    if "race" in low or "races" in low:
        out["track_only"] = True

    # year range
    yf, yt = _extract_year_range(q)
    out["year_from"], out["year_to"] = yf, yt

    # genders
    g_tokens = []
    for tok in re.findall(r"[A-Za-z]+", q):
        lt = tok.lower()
        if lt in GENDER_CANONICAL:
            g_tokens.append(GENDER_CANONICAL[lt])
    out["genders"] = sorted(set(g_tokens))

    # meets
    out["meets"] = _find_multi_targets(low, MEET_CANONICAL)

    # events
    out["events"] = _extract_events_anywhere(q)

    # schools after 'from/at/by'
    for m in re.finditer(r"\b(?:from|at|by)\s+([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)+)", q):
        out["schools"].append(m.group(1).strip())
    # quoted names (athletes or schools)
    for m in re.finditer(r"\"([^\"]+)\"", q):
        out["athletes"].append(m.group(1).strip())
    m = re.search(r"\bby\s+([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)+)", q)
    if m: out["athletes"].append(m.group(1).strip())

    # de-dup
    out["schools"] = list(dict.fromkeys(out["schools"]))
    out["athletes"] = list(dict.fromkeys(out["athletes"]))
    return out

def apply_multi_filters(df: pd.DataFrame, f: Dict[str, Optional[str]]) -> pd.DataFrame:
    cur = df.copy()
    if f["genders"]:
        cur = cur[cur["gender"].isin(f["genders"])]
    if f["meets"]:
        cur = cur[cur["meet"].isin(f["meets"])]
    if f["events"]:
        cur = cur[cur["event"].isin(f["events"])]
    if f["schools"]:
        mask = False
        for s in f["schools"]:
            needle = s.lower()
            mask = mask | cur["school"].str.lower().str.contains(needle, na=False)
        cur = cur[mask]
    if f["athletes"]:
        mask = False
        for a in f["athletes"]:
            needle = a.lower()
            mask = mask | cur["name"].str.lower().str.contains(needle, na=False)
        cur = cur[mask]
    yf, yt = f.get("year_from"), f.get("year_to")
    if yf and yt:
        cur = cur[(cur["year"] >= yf) & (cur["year"] <= yt)]
    elif yf and not yt:
        cur = cur[cur["year"] >= yf]
    # “races” → track only
    if f.get("track_only"):
        cur = cur[cur["event"].isin(TRACK_EVENTS)]
    return cur.sort_values(["gender","event","meet","year"], ascending=[True,True,True,False])

def leaderboard_wins(df: pd.DataFrame, f: Dict[str, Optional[str]]) -> pd.DataFrame:
    """Aggregate wins by athlete (Name + School + Gender) using the same filters as champions."""
    cur = apply_multi_filters(df, f)
    # If no events specified but "track_only" is set, we've already filtered above.
    grp = (cur.groupby(["gender","name","school"])
              .size()
              .reset_index(name="wins")
              .sort_values(["wins","gender","name"], ascending=[False, True, True]))
    top_n = f.get("top_n", 10)
    return grp.head(top_n)

# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="DE HS Track & Field Champions Q&A", page_icon="🏃", layout="wide")
st.title("Delaware HS Track & Field — Champions Q&A")

# ---------- Bundled, path-safe loader ----------
APP_DIR = Path(__file__).parent
BUNDLED_XLSX_NAME = "Delaware Track and Field Supersheet (6).xlsx"
BUNDLED_XLSX_PATH = APP_DIR / BUNDLED_XLSX_NAME

with st.sidebar:
    st.header("Data source")
    if not BUNDLED_XLSX_PATH.exists():
        st.error(
            f"Bundled workbook not found at:\n{BUNDLED_XLSX_PATH}\n\n"
            "• Ensure the file is in the repo alongside streamlit_app.py\n"
            "• Verify the name matches exactly (including spaces & parentheses)."
        )
        df = None; mvps_df = None; KNOWN_SCHOOLS = set()
    else:
        try:
            with open(BUNDLED_XLSX_PATH, "rb") as f:
                file_bytes = f.read()
            df = load_champions(file_bytes)
            mvps_df = load_mvps(file_bytes)
            # Known schools for auto-detect in prompts (also helps MVP filtering)
            KNOWN_SCHOOLS = {s for s in df["school"].dropna().unique()}
            st.success(f"Loaded champions: {len(df):,} rows")
            st.success(f"Loaded MVPs: {len(mvps_df):,} rows")
            st.caption(f"Workbook: {BUNDLED_XLSX_NAME}")
        except Exception as ex:
            st.error("There was a problem parsing the bundled workbook.")
            st.exception(ex)
            df = None; mvps_df = None; KNOWN_SCHOOLS = set()
# ------------------------------------------------

tab1, tab2, tab3, tab4, tab5 = st.tabs(["🔎 Ask a question", "🎛️ Explore", "👤 Athlete profiles", "🏆 MVPs", "🛠️ Data status"])

# ----------------------------
# Q&A — with multi-condition engine + leaderboards + intents
# ----------------------------
with tab1:
    st.subheader("Natural-language Q&A")
    st.caption(
        "Examples: “Who won the girls 200 at Indoor in 2026?”, "
        "“How many state championships has Juliana Balon won?”, "
        "“List every cross country state MVP from Tatnall”, "
        "“Who has won the most Division I races?”, "
        "“Girls long jump or triple jump state champions at Padua since 2018”, "
        "“List Padua sprint winners 2022–2026 at Indoor”."
    )
    q = st.text_input("Type your question")
    if q and df is not None:
        f_multi = parse_question_multi(q)

        # ---- Title-count intent (unchanged) ----
        if f_multi.get("intent") == "count_titles" and f_multi.get("athletes"):
            scope = f_multi.get("scope")
            if scope == "indoor":
                include_meets = STATE_MEETS_INDOOR; scope_label = "Indoor state championships"
            elif scope == "outdoor":
                include_meets = STATE_MEETS_OUTDOOR; scope_label = "Outdoor state championships"
            else:
                include_meets = STATE_MEETS_ALL if ("state" in (f_multi.get("scope") or "") or "state" in q.lower()) else set(df["meet"].unique())
                scope_label = "State championships" if include_meets == STATE_MEETS_ALL else "Championships (all meets)"
            if f_multi["genders"]:
                df_scope = df[df["gender"].isin(f_multi["genders"])]
                total_count, rows = title_count(df_scope, f_multi["athletes"][0], include_meets=include_meets, include_relays=False)
                st.subheader(f"{f_multi['athletes'][0]} — {scope_label}")
                st.metric("Titles", total_count)
                if total_count > 0:
                    c1,c2,c3 = st.columns(3)
                    with c1: st.caption("By meet");  st.dataframe(rows.groupby("meet").size().reset_index(name="titles"))
                    with c2: st.caption("By event"); st.dataframe(rows.groupby("event").size().reset_index(name="titles"))
                    with c3: st.caption("By year");  st.dataframe(rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
                    st.caption("All title rows (relays excluded)")
                    st.dataframe(rows[["gender","year","meet","event","class","school","mark"]], use_container_width=True)
                st.stop()
            else:
                genders = guess_gender_for_name(df, f_multi["athletes"][0])
                st.subheader(f"{f_multi['athletes'][0]} — {scope_label}")
                for g in genders:
                    c, rws = title_count(df[df["gender"] == g], f_multi["athletes"][0], include_meets=include_meets, include_relays=False)
                    st.metric(f"{g.title()} titles", c)
                    if c > 0:
                        st.dataframe(rws[["gender","year","meet","event","class","school","mark"]], use_container_width=True)
                st.stop()

        # ---- MVP intent (now supports school filter) ----
        if f_multi.get("intent") == "mvp_lookup" and mvps_df is not None:
            scope_map = {"indoor": "Indoor", "outdoor": "Outdoor", "cross country": "Cross Country"}
            mvps_scope = scope_map.get(f_multi.get("scope"), None)
            cur = mvps_df.copy()
            if mvps_scope: cur = cur[cur["scope"] == mvps_scope]
            if f_multi["genders"]: cur = cur[cur["gender"].isin(f_multi["genders"])]
            # school filter (substring)
            if f_multi["schools"]:
                mask = False
                for s in f_multi["schools"]:
                    needle = s.lower()
                    mask = mask | cur["school"].str.lower().str.contains(needle, na=False)
                cur = cur[mask]
            # if no school given, auto-detect from prompt against known schools
            if not f_multi["schools"] and KNOWN_SCHOOLS:
                lowp = f_multi["raw"].lower()
                auto = []
                for s in KNOWN_SCHOOLS:
                    if isinstance(s,str) and s.lower() in lowp:
                        auto.append(s)
                if auto:
                    mask = False
                    for s in auto:
                        needle = s.lower()
                        mask = mask | cur["school"].str.lower().str.contains(needle, na=False)
                    cur = cur[mask]
            # year filters (use season_end as inclusive “since”)
            yf, yt = f_multi["year_from"], f_multi["year_to"]
            if yf and yt:
                cur = cur[(cur["season_end"] >= yf) & (cur["season_start"] <= yt)]
            elif yf and not yt:
                cur = cur[cur["season_end"] >= yf]
            # Output
            if yf and yt and yf == yt:
                if cur.empty: st.error("No MVP found for that combination. Try adjusting gender/scope/year/school.")
                else:
                    st.success("MVP result")
                    st.dataframe(cur[["season_label","gender","scope","name","school","category"]], use_container_width=True)
                st.stop()
            else:
                if cur.empty: st.warning("No MVPs matched your filters.")
                else:
                    st.dataframe(cur.sort_values(["season_end","gender","scope"])[["season_label","gender","scope","name","school","category"]],
                                 use_container_width=True)
                st.stop()

        # ---- Leaderboard intent: "Who has won the most …" ----
        if f_multi.get("intent") == "leaderboard_wins":
            # “state” rule if no indoor/outdoor explicitly mentioned
            lowp = f_multi["raw"].lower()
            if ("state" in lowp) and ("indoor" not in lowp) and ("outdoor" not in lowp) and not f_multi["meets"]:
                f_multi["meets"] = list(STATE_MEETS_ALL)

            # If “Division I” etc. is present, already captured in f_multi["meets"].
            # Auto-detect schools (helps prompts like “… from Tatnall” that weren't parsed)
            if not f_multi["schools"] and KNOWN_SCHOOLS:
                lowq = f_multi["raw"].lower()
                for s in KNOWN_SCHOOLS:
                    if isinstance(s,str) and s.lower() in lowq:
                        f_multi["schools"].append(s)

            lb = leaderboard_wins(df, f_multi)
            if lb.empty:
                st.error("No matching winners found for your leaderboard filters.")
                with st.expander("Detected filters"):
                    st.json(f_multi)
            else:
                title = "Top winners"
                if f_multi["meets"]: title += f" — {', '.join(f_multi['meets'])}"
                if f_multi["events"]: title += f" | Events: {', '.join(sorted(f_multi['events']))}"
                st.subheader(title)
                st.dataframe(lb.reset_index(drop=True), use_container_width=True)
            st.stop()

        # ---- Champions multi-condition fallback ----
        low = f_multi["raw"].lower()
        if ("state" in low) and ("indoor" not in low) and ("outdoor" not in low) and not f_multi["meets"]:
            f_multi["meets"] = list(STATE_MEETS_ALL)
        if ({"100/55","100/55H","110/55H"} & f_multi["events"]) and not f_multi["meets"]:
            f_multi["meets"] = list(STATE_MEETS_INDOOR)
        if not f_multi["schools"] and KNOWN_SCHOOLS:
            lowp = f_multi["raw"].lower()
            for s in KNOWN_SCHOOLS:
                if isinstance(s,str) and s.lower() in lowp:
                    f_multi["schools"].append(s)

        result = apply_multi_filters(df, f_multi)

        if result.empty:
            st.error("No matches found. Try adjusting events/meets/schools/years.")
            with st.expander("Detected filters"):
                st.json(f_multi)
        else:
            if (f_multi.get("year_from") == f_multi.get("year_to")
                and len(f_multi.get("events", [])) == 1
                and len(result) == 1):
                row = result.iloc[0]
                st.success(
                    f"**{row['gender'].title()} {row['event']} — {row['meet']} {row['year']}**\n\n"
                    f"🏅 **{row['name']}** ({row['class']}) — {row['school']} — **{row['mark']}**"
                )
            st.dataframe(
                result[["gender","event","meet","year","name","class","school","mark"]].reset_index(drop=True),
                use_container_width=True,
            )

# ----------------------------
# Explore
# ----------------------------
with tab2:
    st.subheader("Filter champions")
    if df is None:
        st.info("Bundled workbook not loaded.")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        g = c1.selectbox("Gender", options=["(any)"] + sorted(df["gender"].unique().tolist()))
        m = c2.selectbox("Meet", options=["(any)"] + sorted(df["meet"].unique().tolist()))
        ev_all = sorted(df["event"].unique().tolist())
        ev = c3.selectbox("Event", options=["(any)"] + ev_all)
        yrs = sorted(df["year"].dropna().unique().tolist(), reverse=True)
        y = c4.selectbox("Year", options=["(any)"] + yrs)
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
        st.dataframe(
            cur.sort_values(["gender","event","meet","year"], ascending=[True,True,True,False]).reset_index(drop=True),
            use_container_width=True
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
                    with c1: st.caption("By meet");  st.dataframe(all_rows.groupby("meet").size().reset_index(name="titles"))
                    with c2: st.caption("By event"); st.dataframe(all_rows.groupby("event").size().reset_index(name="titles"))
                    with c3: st.caption("By year");  st.dataframe(all_rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
                    st.caption("Title rows (relays excluded)")
                    st.dataframe(all_rows[["gender","year","meet","event","class","school","mark"]], use_container_width=True)
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
        st.dataframe(cur[["season_label","gender","scope","name","school"]], use_container_width=True)

# ----------------------------
# Data Status
# ----------------------------
with tab5:
    st.subheader("Data status / debug")
    if df is None:
        st.info("No data loaded.")
    else:
        try:
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Champion rows", f"{len(df):,}")
            c2.metric("Min champ year", int(df["year"].min()))
            c3.metric("Max champ year", int(df["year"].max()))
            c4.metric("MVP rows", 0 if (mvps_df is None) else len(mvps_df))
        except Exception:
            st.metric("Champion rows", f"{len(df):,}")
        st.write("Meets (champions):", sorted(df["meet"].unique()))
        st.write("Events (sample):", sorted(df["event"].unique())[:16])
        if mvps_df is not None and not mvps_df.empty:
            st.write("MVP scopes:", sorted(mvps_df["scope"].unique().tolist()))
            st.write("MVP seasons range:", f"{int(mvps_df['season_end'].min())} → {int(mvps_df['season_end'].max())}")
        st.write("Champions sample:")
        st.dataframe(df.head(20), use_container_width=True)
