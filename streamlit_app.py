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
#   • Leaderboards: "Who has won the most …" (supports 'won', defaults to Top‑1 on "who … most")
#   • "State" rule: if prompt contains 'state' but NOT 'indoor'/'outdoor',
#       default meets → Division I, Division II, Indoor State Championship
#   • MVPs tab + Data Status tab
#   • Quick example chips + deep-linking (?q=)
#   • FIXES/ADDITIONS (this version):
#       - Meet canonicalization during parsing
#       - Leaderboard intent detects both “most … won” and “won … most”
#       - Robust “Who was the last … to win …” and “When was the last time … won …”
#       - Accurate event selection for phrases like "long jump or triple jump" (no HJ bleed-through)
#       - Uniform table rendering (hide index; Girls/Boys; Gender first)
#       - SAME-MEET SWEEP detection when multiple events + "last"/"to win"
#       - "Clear question" button
#       - Highlight parsed entities above results

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
    "Indoor State Championship": {
        "indoor", "indoor state", "state indoor", "indoor championship",
        "indoor state championship",      # singular
        "indoor state championships"      # plural
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

# ---------- Event groups (UPDATED per requested definitions) ----------
# Distance: 800, 1600, 3200
# Sprints: 100/55, 110/55H, 200, 400, 300H
# Hurdles: 110/55H, 300H
# Field: HJ, LJ, TJ, Shot put, Discus, PV
# Jumps: HJ, LJ, TJ
# Throws: Shot put, Discus
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
    # sprints
    "sprint": "sprints", "sprinters": "sprints", "sprints": "sprints",
    # distance
    "distance": "distance", "distances": "distance", "distance events": "distance",
    # hurdles
    "hurdle": "hurdles", "hurdles": "hurdles", "hurdling": "hurdles",
    # field
    "field": "field", "field events": "field",
    # jumps
    "jump": "jumps", "jumps": "jumps", "field jumps": "jumps",
    # throws
    "throw": "throws", "throws": "throws",
    # relays
    "relay": "relays", "relays": "relays", "4x": "relays",
}

# Track vs field (for "races" leaderboard)
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
            # Canonicalize meet names (accept synonyms, pluralization, etc.)
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
# MVPs parsing (school to right; co‑MVPs within a season block)
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
# Table presentation helpers (uniform: hide index; Girls/Boys; Gender first)
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
    # IMPORTANT: avoid global group synonym injection (prevents HJ leakage from "long jump")
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

    # ---- Intents --------------------------
    # Count titles
    if re.search(r"\bhow many\b.*\b(championships?|titles?)\b", low):
        out["intent"] = "count_titles"
        if "state" in low:   out["scope"] = "state"
        if "indoor" in low:  out["scope"] = "indoor"
        if "outdoor" in low: out["scope"] = "outdoor"

    # MVP
    if "mvp" in low or "most valuable" in low:
        out["intent"] = "mvp_lookup"
        if "indoor" in low:          out["scope"] = "indoor"
        elif "outdoor" in low:       out["scope"] = "outdoor"
        elif "cross country" in low: out["scope"] = "cross country"

    # Leaderboard — detect both orders: "most ... won" OR "won ... most"
    if (re.search(r"\b(most|record)\b.*\b(win|wins|won|titles?|races?)\b", low) or
        re.search(r"\b(win|wins|won|titles?|races?)\b.*\b(most|record)\b", low) or
        re.search(r"\btop\s+\d+\b", low)):
        out["intent"] = "leaderboard_wins"

    # "When was the last time ... won ..." (last_win_time)
    if re.search(r"\bwhen was the last time\b.*\bwon\b", low) or re.search(r"\bmost recent\b.*\bwin\b", low):
        out["intent"] = "last_win_time"

    # "Who was the last ... to win ..." (covers more phrases)
    if re.search(r"\bwho was the last\b.*\bto win\b", low):
        out["intent"] = "last_win_time"
    # ---------------------------------

    # top N (default Top‑1 if "who … most")
    m_top = re.search(r"\btop\s+(\d+)\b", low)
    if m_top:
        out["top_n"] = max(1, int(m_top.group(1)))
    if out["intent"] == "leaderboard_wins" and not m_top and re.search(r"\bwho\b", low) and re.search(r"\bmost\b", low):
        out["top_n"] = 1

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

    # ATHLETES:
    # 1) quoted names
    for m in re.finditer(r"\"([^\"]+)\"", q):
        out["athletes"].append(m.group(1).strip())
    # 2) common lead-ins: has/did/for/by/from/at <First Last ...>
    m = re.search(r"\b(?:has|did|for|by|from|at)\s+([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)+)", q)
    if m:
        out["athletes"].append(m.group(1).strip())
    # 3) fallback 'by <Name>'
    m = re.search(r"\bby\s+([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)+)", q)
    if m:
        out["athletes"].append(m.group(1).strip())

    # de-dup
    out["schools"]  = list(dict.fromkeys(out["schools"]))
    out["athletes"] = list(dict.fromkeys(out["athletes"]))

    # SAME-MEET SWEEP detection:
    # If there are TWO+ events and the question is about "last"/"most recent"
    # or uses "who was the last ... to win", treat as same-meet sweep.
    if len(out["events"]) >= 2:
        if (out["intent"] in ("last_win_time", "last_sweep")) or \
           re.search(r"\b(last|most recent)\b", low) or \
           re.search(r"\bto win\b", low):
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
    """Aggregate wins by athlete (Name + School + Gender) using the same filters as champions."""
    cur = apply_multi_filters(df, f)
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
        df = None; mvps_df = None; KNOWN_SCHOOLS = set(); KNOWN_ATHLETES = set()
    else:
        try:
            with open(BUNDLED_XLSX_PATH, "rb") as f:
                file_bytes = f.read()
            df = load_champions(file_bytes)
            mvps_df = load_mvps(file_bytes)
            # Known schools/athletes for auto-detect in prompts
            KNOWN_SCHOOLS  = {s for s in df["school"].dropna().unique()}
            KNOWN_ATHLETES = {n for n in df["name"].dropna().unique()}
            st.success(f"Loaded champions: {len(df):,} rows")
            st.success(f"Loaded MVPs: {len(mvps_df):,} rows")
            st.caption(f"Workbook: {BUNDLED_XLSX_NAME}")
        except Exception as ex:
            st.error("There was a problem parsing the bundled workbook.")
            st.exception(ex)
            df = None; mvps_df = None; KNOWN_SCHOOLS = set(); KNOWN_ATHLETES = set()
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
        "“Who has won the boys 200 the most at the indoor state championships?”, "
        "“List Padua sprint winners 2022–2026 at Indoor”."
    )

    # ---- Deep-linking (?q=) with safe fallback ----
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

    # Prime from URL if no prefill yet
    if "q_prefill" not in st.session_state or not st.session_state["q_prefill"]:
        url_q = _get_q_from_url()
        if url_q:
            st.session_state["q_prefill"] = url_q

    # ---- QUICK EXAMPLE CHIPS ----
    example_prompts = [
        "Who won the girls 200 at Indoor in 2026?",
        "How many state championships has Juliana Balon won?",
        "Who has won the most Division I races?",
        "Girls long jump or triple jump state champions at Padua since 2018",
        # Hard cases:
        "Who has won the boys 200 the most at the indoor state championships?",
        "Who has won the boys 400 the most at the Meet of Champions?",
        "When was the last time a Middletown runner won the Meet of Champions 400?",
        "When was the last time a girl won the 800, 1600 and 3200 at a single indoor track and field state meet?",
        "Who was the last girl to win the 55H, 55 and 200 at the indoor track and field state meet?",
    ]
    st.caption("Quick examples:")
    ex_cols = st.columns(2)
    for i, ex in enumerate(example_prompts):
        if ex_cols[i % 2].button(ex):
            st.session_state["q_prefill"] = ex
            _set_q_in_url(ex)
            st.rerun()

    # ---- Standard Question Bar + Clear button (NEW) ----
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

    # ---- "What I understood" helper (NEW) ----
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
            }.get(intent, intent)
            parts.insert(0, f"**Intent:** {label}")
        if parts:
            st.markdown("🧠 *What I understood:* " + " — ".join(parts))

    if q and df is not None:
        f_multi = parse_question_multi(q)

        # Helpers
        def _apply_state_default(fm: Dict[str, Optional[str]], text: str):
            lowx = text.lower()
            if ("state" in lowx) and ("indoor" not in lowx) and ("outdoor" not in lowx) and not fm["meets"]:
                fm["meets"] = list(STATE_MEETS_ALL)

        def _auto_add_schools(fm: Dict[str, Optional[str]]):
            if not fm["schools"] and KNOWN_SCHOOLS:
                lowq = fm["raw"].lower()
                for s in KNOWN_SCHOOLS:
                    if isinstance(s, str) and s.lower() in lowq:
                        fm["schools"].append(s)

        # Show understood entities right away (NEW)
        _render_understood(f_multi)

        # ---- Title-count intent ----
        if f_multi.get("intent") == "count_titles":
            # If no athlete parsed, auto-detect from known athletes
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
                st.subheader(f"{athlete} — {scope_label}")
                st.metric("Titles", total_count)
                if total_count > 0:
                    c1,c2,c3 = st.columns(3)
                    with c1:
                        st.caption("By meet")
                        show_table(rows.groupby("meet").size().reset_index(name="titles"))
                    with c2:
                        st.caption("By event")
                        show_table(rows.groupby("event").size().reset_index(name="titles"))
                    with c3:
                        st.caption("By year")
                        show_table(rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
                    st.caption("All title rows (relays excluded)")
                    show_table(rows[["gender","year","meet","event","class","school","mark"]])
                st.stop()
            else:
                genders = guess_gender_for_name(df, athlete)
                st.subheader(f"{athlete} — {scope_label}")
                for g in genders:
                    c, rws = title_count(df[df["gender"] == g], athlete, include_meets=include_meets, include_relays=False)
                    st.metric(f"{g.title()} titles", c)
                    if c > 0:
                        show_table(rws[["gender","year","meet","event","class","school","mark"]])
                st.stop()

        # ---- MVP intent ----
        if f_multi.get("intent") == "mvp_lookup" and mvps_df is not None:
            scope_map = {"indoor": "Indoor", "outdoor": "Outdoor", "cross country": "Cross Country"}
            mvps_scope = scope_map.get(f_multi.get("scope"), None)
            cur = mvps_df.copy()
            if mvps_scope: cur = cur[cur["scope"] == mvps_scope]
            if f_multi["genders"]: cur = cur[cur["gender"].isin(f_multi["genders"])]

            # School filter (explicit or auto-detect from known schools)
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

            # Year filters (use season_end for "since")
            yf, yt = f_multi["year_from"], f_multi["year_to"]
            if yf and yt:
                cur = cur[(cur["season_end"] >= yf) & (cur["season_start"] <= yt)]
            elif yf and not yt:
                cur = cur[cur["season_end"] >= yf]

            if yf and yt and yf == yt:
                if cur.empty: st.error("No MVP found for that combination. Try adjusting gender/scope/year/school.")
                else:
                    st.success("MVP result")
                    show_table(cur[["gender","scope","season_label","name","school","category"]])
                st.stop()
            else:
                if cur.empty: st.warning("No MVPs matched your filters.")
                else:
                    show_table(cur.sort_values(["season_end","gender","scope"])[["gender","scope","season_label","name","school","category"]])
                st.stop()

        # ---- LAST-TIME intent (single event) ----
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
            st.success(f"**Last time:** {latest_year}")
            show_table(latest_rows[["gender","year","meet","event","name","school","class","mark"]])
            st.stop()

        # ---- LAST-SWEEP intent (multi-event, same meet/year) ----
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
                with st.expander("Detected filters"): st.json(f_multi)
                st.stop()

            # SAME-MEET SWEEP: same year+meet+gender+athlete must include ALL required events
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
            st.success(f"**Most recent sweep:** {last_year} — {winners}")
            detail = cur[cur["year"] == last_year]
            detail = detail.merge(last_hits[["year","meet","gender","name"]], on=["year","meet","gender","name"], how="inner")
            detail = detail.sort_values(["gender","meet","name","event"])
            show_table(detail[["gender","year","meet","name","event","school","class","mark"]])
            st.stop()

        # ---- Leaderboard intent: "Who has won the most …" ----
        if f_multi.get("intent") == "leaderboard_wins":
            _apply_state_default(f_multi, f_multi["raw"])
            _auto_add_schools(f_multi)

            lb = leaderboard_wins(df, f_multi)

            # Top‑1: show a leader card
            if f_multi.get("top_n", 10) == 1 and not lb.empty:
                row = lb.iloc[0]
                bits = []
                if f_multi["genders"]: bits.append("/".join([g.title() for g in f_multi["genders"]]))
                if f_multi["events"]:  bits.append(", ".join(sorted(f_multi["events"])))
                if f_multi["meets"]:   bits.append(", ".join(f_multi["meets"]))
                title_text = " — ".join([b for b in bits if b])
                st.success(
                    f"**Most wins {('— ' + title_text) if title_text else ''}**\n\n"
                    f"🏆 **{row['name']}** — {row['school']} — *{row['gender'].title()}* — **{int(row['wins'])} wins**"
                )
                st.stop()

            if lb.empty:
                st.error("No matching winners found for your leaderboard filters.")
                with st.expander("Detected filters"): st.json(f_multi)
            else:
                st.subheader("Top winners")
                show_table(lb.reset_index(drop=True)[["gender","name","school","wins"]])
            st.stop()

        # ---- Champions multi-condition fallback ----
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
                st.success(
                    f"**{row['gender'].title()} {row['event']} — {row['meet']} {row['year']}**\n\n"
                    f"🏅 **{row['name']}** ({row['class']}) — {row['school']} — **{row['mark']}**"
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
                    with c1:
                        st.caption("By meet")
                        show_table(all_rows.groupby("meet").size().reset_index(name="titles"))
                    with c2:
                        st.caption("By event")
                        show_table(all_rows.groupby("event").size().reset_index(name="titles"))
                    with c3:
                        st.caption("By year")
                        show_table(all_rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
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
        show_table(df.head(20))
