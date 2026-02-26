# streamlit_app.py
# ---------------------------------------------------------
# Delaware HS Track & Field Champions Q&A — Streamlit
# Bundled workbook build (no upload required).
#
# Features:
#   • Bundled, path-safe loader for the Excel workbook
#   • Parses GIRLS + BOYS champions into a normalized table
#   • Natural-language Q&A (events/meets/years + "how many state titles ...")
#   • Athlete Profiles tab: title counts & breakdowns (relays excluded)
#   • MVPs parsing + Q&A ("who was indoor/outdoor boys/girls MVP in YEAR",
#     "list ... since YEAR")
#   • MVPs tab for manual browsing

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

# Meet names + common synonyms
MEETS: Dict[str, set] = {
    "Division I": {"division 1", "div i", "d1"},
    "Division II": {"division 2", "div ii", "d2"},
    "Meet of Champions": {"moc", "meet of champs", "meet of champion"},
    "New Castle County": {"ncc", "new castle", "new castle co"},
    "Henlopen Conference": {"henlopen"},
    "Indoor State Championship": {"indoor", "indoor state", "state indoor", "indoor championship"},
}

# Build a canonical lookup for meet phrases
MEET_CANONICAL: Dict[str, str] = {}
for canonical, synonyms in MEETS.items():
    MEET_CANONICAL[canonical.lower()] = canonical
    for s in synonyms:
        MEET_CANONICAL[s.lower()] = canonical

# Event names + common synonyms
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

# Build a canonical lookup for events
EVENT_CANONICAL: Dict[str, str] = {}
for canonical, synonyms in EVENTS.items():
    EVENT_CANONICAL[canonical.lower()] = canonical
    for s in synonyms:
        EVENT_CANONICAL[s.lower()] = canonical

# Gender aliases
GENDER_ALIASES: Dict[str, set] = {
    "girls": {"girls", "girl", "g", "women", "female"},
    "boys": {"boys", "boy", "b", "men", "male"},
}

# Build a canonical lookup for gender tokens
GENDER_CANONICAL: Dict[str, str] = {}
for canonical, synonyms in GENDER_ALIASES.items():
    GENDER_CANONICAL[canonical] = canonical.upper()
    for s in synonyms:
        GENDER_CANONICAL[s] = canonical.upper()

# ----------------------------
# State-meet definitions
# ----------------------------
STATE_MEETS_OUTDOOR = {"Division I", "Division II"}
STATE_MEETS_INDOOR = {"Indoor State Championship"}
STATE_MEETS_ALL = STATE_MEETS_OUTDOOR | STATE_MEETS_INDOOR

# ----------------------------
# Workbook parsing helpers — CHAMPIONS
# ----------------------------
def detect_year_bundles(ws) -> List[Tuple[int, int]]:
    """Return list of (year, start_col) for each 4-column bundle on row 1."""
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
    """Normalize the column A event label to a canonical event string."""
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
    """Parse GIRLS or BOYS sheet into normalized champions rows."""
    year_bundles = detect_year_bundles(ws)
    records = []

    current_event: Optional[str] = None
    for r in range(1, ws.max_row + 1):
        ev_raw = ws.cell(row=r, column=1).value  # Column A
        if ev_raw:
            maybe_ev = normalize_event_label(ev_raw)
            if maybe_ev:
                current_event = maybe_ev

        meet_raw = ws.cell(row=r, column=5).value  # Column E
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
    """Load Excel bytes and parse GIRLS + BOYS champions into a single DataFrame."""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    girls_df = parse_champions_sheet(wb["GIRLS"], "GIRLS")
    boys_df = parse_champions_sheet(wb["BOYS"], "BOYS")
    out = pd.concat([girls_df, boys_df], ignore_index=True)
    out["event"] = out["event"].astype(str).str.replace(r"\.0$", "", regex=True)
    return out

# ----------------------------
# Workbook parsing helpers — MVPs
# ----------------------------
MVP_CATEGORY_MAP = {
    "Girls Indoor Track and Field": ("GIRLS", "Indoor"),
    "Boys Indoor Track and Field": ("BOYS", "Indoor"),
    "Girls Outdoor Track and Field": ("GIRLS", "Outdoor"),
    "Boys Outdoor Track and Field": ("BOYS", "Outdoor"),
    # Optional (kept for completeness; you can use the tab to browse these):
    "Girls Cross Country": ("GIRLS", "Cross Country"),
    "Boys Cross Country": ("BOYS", "Cross Country"),
}

def _find_header_row(ws) -> Optional[int]:
    """Find header row containing 'Year' and at least one MVP category header."""
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
    """
    Convert season label like '2024-25' → (2024, 2025, '2024-25').
    If '2010' → (2010, 2010, '2010').
    """
    if lbl is None:
        return None, None, None
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
    """
    Parse the MVPs sheet into a tidy DataFrame with columns:
      season_label, season_start, season_end, category, gender, scope, name, school
    Robust to the two-row per season pattern (name row then school row).
    """
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

    headers = {}
    for c in range(1, ws.max_column + 1):
        val = ws.cell(row=header_row, column=c).value
        if val:
            headers[str(val).strip()] = c

    target_cols = {cat: col for cat, col in headers.items() if cat in MVP_CATEGORY_MAP}

    records = []
    r = header_row + 1
    while r <= ws.max_row:
        year_cell = ws.cell(row=r, column=1).value
        y1, y2, season_label = _parse_season_label(year_cell)
        if season_label is None:
            r += 1
            continue
        # Row r: names; Row r+1: schools
        for cat, col in target_cols.items():
            name = ws.cell(row=r, column=col).value
            school = ws.cell(row=r + 1, column=col).value if r + 1 <= ws.max_row else None
            if name:
                gender, scope = MVP_CATEGORY_MAP[cat]
                records.append({
                    "season_label": season_label,
                    "season_start": y1,
                    "season_end": y2,
                    "category": cat,
                    "gender": gender,
                    "scope": scope,   # 'Indoor' | 'Outdoor' | 'Cross Country'
                    "name": str(name).strip(),
                    "school": str(school).strip() if school else None,
                })
        r += 2

    df = pd.DataFrame.from_records(records).drop_duplicates().reset_index(drop=True)
    return df

# ----------------------------
# Athlete/title utilities
# ----------------------------
def normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower()) if isinstance(s, str) else ""


@st.cache_data(show_spinner=False)
def all_athletes_index(df: pd.DataFrame) -> pd.DataFrame:
    """Return distinct athletes with (gender, name, seen schools)."""
    tmp = df.copy()
    tmp["name_norm"] = tmp["name"].apply(normalize_name)
    schools = (
        tmp.groupby(["gender", "name", "name_norm"])["school"]
        .apply(lambda x: ", ".join(sorted({s for s in x.dropna()})))
        .reset_index(name="schools")
    )
    return schools.sort_values(["gender", "name"]).reset_index(drop=True)


def title_count(
    df: pd.DataFrame,
    athlete_name: str,
    *,
    include_meets: set,
    include_relays: bool = False,  # relays excluded for athlete profiles/counts
) -> tuple[int, pd.DataFrame]:
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
# Natural-language parsing (incl. MVP intent)
# ----------------------------
def canonical_meet(token: str) -> Optional[str]:
    low = token.strip().lower()
    if low in MEET_CANONICAL:
        return MEET_CANONICAL[low]
    choice = difflib.get_close_matches(low, list(MEET_CANONICAL.keys()), n=1, cutoff=0.85)
    if choice:
        return MEET_CANONICAL[choice[0]]
    return None


def canonical_event(event_text: str, gender: Optional[str]) -> Optional[str]:
    t = event_text.strip().lower()
    if t in EVENT_CANONICAL:
        return EVENT_CANONICAL[t]
    # Hurdles ambiguity
    if "hurd" in t or t in {"55h", "110h", "100h"}:
        if gender == "GIRLS":
            return "100/55H"
        if gender == "BOYS":
            return "110/55H"
        if "55" in t:
            return "100/55H"
    # 55 dash ambiguity
    if t in {"55", "55m"}:
        return "100/55"
    # fuzzy fallback
    canon_keys = list({v.lower(): v for v in EVENT_CANONICAL.values()}.keys())
    m = difflib.get_close_matches(t, canon_keys, n=1, cutoff=0.8)
    if m:
        return EVENT_CANONICAL[m[0]]
    return None


def parse_question(q: str) -> Dict[str, Optional[str]]:
    """
    Extract filters + 'intent' for title counting and MVP lookups.
    Fields: gender, event, meet, year, since_year, name, school, intent, scope
    scope ∈ {None, 'state', 'indoor', 'outdoor', 'cross country'}
    """
    out = {
        "gender": None, "event": None, "meet": None, "year": None, "since_year": None,
        "name": None, "school": None, "intent": None, "scope": None
    }
    t = q.strip()
    low = t.lower()

    # intent: count titles
    if re.search(r"\bhow many\b.*\b(championships?|titles?)\b", low):
        out["intent"] = "count_titles"
        if "state" in low:   out["scope"] = "state"
        if "indoor" in low:  out["scope"] = "indoor"
        if "outdoor" in low: out["scope"] = "outdoor"

    # intent: MVP lookup/list
    if "mvp" in low or "most valuable" in low:
        out["intent"] = "mvp_lookup"
        if "indoor" in low:          out["scope"] = "indoor"
        elif "outdoor" in low:       out["scope"] = "outdoor"
        elif "cross country" in low: out["scope"] = "cross country"

    # year "in 2010"
    y_in = re.search(r"\bin\s*(20\d{2})\b", low)
    if y_in:
        out["year"] = int(y_in.group(1))

    # "since 2010" / "after 2010" / "from 2010"
    y_since = re.search(r"\b(since|after|from)\s*(20\d{2})\b", low)
    if y_since:
        out["since_year"] = int(y_since.group(2))

    # bare year if not already captured
    if not out["year"]:
        bare = re.findall(r"\b(20\d{2})\b", low)
        if bare:
            out["year"] = int(bare[0])

    # gender
    for tok in re.findall(r"[A-Za-z]+", t):
        low_tok = tok.lower()
        if low_tok in GENDER_CANONICAL:
            out["gender"] = GENDER_CANONICAL[low_tok]
            break

    # meet (for champions path)
    for phrase in sorted(MEET_CANONICAL.keys(), key=len, reverse=True):
        if phrase in low:
            out["meet"] = MEET_CANONICAL[phrase]
            break

    # event
    for ev_phrase in sorted(EVENT_CANONICAL.keys(), key=len, reverse=True):
        if ev_phrase in low:
            out["event"] = EVENT_CANONICAL[ev_phrase]
            break
    if not out["event"]:
        nums = re.findall(r"\b(\d{2,4})\b", t)
        if nums:
            out["event"] = canonical_event(nums[0], out["gender"])

    # athlete / school hint
    m = re.search(r'\"([^\"]+)\"', t)
    if m:
        out["name"] = m.group(1).strip()
    else:
        m2 = re.search(r"(?:has|did|for|by|from|at)\s+([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+)*)", q)
        if m2:
            out["name"] = m2.group(1).strip()

    return out

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
        df = None
        mvps_df = None
    else:
        try:
            with open(BUNDLED_XLSX_PATH, "rb") as f:
                file_bytes = f.read()
            df = load_champions(file_bytes)
            mvps_df = load_mvps(file_bytes)
            st.success(f"Loaded champions: {len(df):,} rows")
            st.success(f"Loaded MVPs: {len(mvps_df):,} rows")
            st.caption(f"Workbook: {BUNDLED_XLSX_NAME}")
        except Exception as ex:
            st.error("There was a problem parsing the bundled workbook.")
            st.exception(ex)
            df = None
            mvps_df = None
# ------------------------------------------------

tab1, tab2, tab3, tab4, tab5 = st.tabs(["🔎 Ask a question", "🎛️ Explore", "👤 Athlete profiles", "🏆 MVPs", "🛠️ Data status"])

# ----------------------------
# Q&A
# ----------------------------
with tab1:
    st.subheader("Natural-language Q&A")
    st.caption(
        "Examples: “Who won the girls 200 at Indoor in 2026?”, "
        "“How many state championships has Juliana Balon won?”, "
        "“Who was the outdoor boys MVP in 2010?”, "
        "“List every indoor state MVP for girls since 2010”."
    )
    q = st.text_input("Type your question")
    if q and df is not None:
        filters = parse_question(q)

        # Helpful default: for '55' dash/hurdles with no meet, assume Indoor
        if (filters.get("event") in {"100/55", "100/55H", "110/55H"}) and not filters.get("meet"):
            filters["meet"] = "Indoor State Championship"

        # ---- Handle 'count_titles' (champions) ----
        if filters.get("intent") == "count_titles" and filters.get("name"):
            scope = filters.get("scope")
            if scope == "indoor":
                include_meets = STATE_MEETS_INDOOR
                scope_label = "Indoor state championships"
            elif scope == "outdoor":
                include_meets = STATE_MEETS_OUTDOOR
                scope_label = "Outdoor state championships"
            else:
                include_meets = (
                    STATE_MEETS_ALL
                    if ("state" in (filters.get("scope") or "") or "state" in q.lower())
                    else set(df["meet"].unique())
                )
                scope_label = "State championships" if include_meets == STATE_MEETS_ALL else "Championships (all meets)"

            df_scope = df if not filters.get("gender") else df[df["gender"] == filters["gender"]]
            total_count, rows = title_count(
                df_scope, filters["name"], include_meets=include_meets, include_relays=False
            )

            if total_count == 0 and not filters.get("gender"):
                genders = guess_gender_for_name(df, filters["name"])
                collected = []
                for g in genders:
                    c, rws = title_count(
                        df[df["gender"] == g], filters["name"], include_meets=include_meets, include_relays=False
                    )
                    if c:
                        collected.append((g, c, rws))
                if collected:
                    st.subheader(f"{filters['name']} — {scope_label}")
                    for (g, c, rws) in collected:
                        st.metric(f"{g.title()} titles", c)
                        st.dataframe(
                            rws[["gender", "year", "meet", "event", "name", "class", "school", "mark"]],
                            use_container_width=True,
                        )
                    st.stop()

            st.subheader(f"{filters['name']} — {scope_label}")
            st.metric("Titles", total_count)
            if total_count > 0:
                colA, colB, colC = st.columns(3)
                with colA:
                    st.caption("By meet")
                    st.dataframe(rows.groupby("meet").size().reset_index(name="titles"))
                with colB:
                    st.caption("By event")
                    st.dataframe(rows.groupby("event").size().reset_index(name="titles"))
                with colC:
                    st.caption("By year")
                    st.dataframe(rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
                st.caption("All title rows (relays excluded)")
                st.dataframe(
                    rows[["gender", "year", "meet", "event", "class", "school", "mark"]],
                    use_container_width=True,
                )
            st.stop()

        # ---- Handle 'mvp_lookup' ----
        if filters.get("intent") == "mvp_lookup" and mvps_df is not None:
            scope_map = {"indoor": "Indoor", "outdoor": "Outdoor", "cross country": "Cross Country"}
            mvps_scope = scope_map.get(filters.get("scope"), None)

            cur = mvps_df.copy()
            if mvps_scope:
                cur = cur[cur["scope"] == mvps_scope]
            if filters.get("gender"):
                cur = cur[cur["gender"] == filters["gender"]]

            if filters.get("year"):
                y = filters["year"]
                cur = cur[(cur["season_start"] == y) | (cur["season_end"] == y)]

            if filters.get("since_year"):
                y0 = filters["since_year"]
                cur = cur[cur["season_end"] >= y0]

            if filters.get("year") and not filters.get("since_year"):
                if cur.empty:
                    st.error("No MVP found for that combination. Try adjusting gender/scope/year.")
                else:
                    st.success("MVP result")
                    st.dataframe(
                        cur[["season_label", "gender", "scope", "name", "school", "category"]],
                        use_container_width=True,
                    )
                st.stop()
            else:
                if cur.empty:
                    st.warning("No MVPs matched your filters.")
                else:
                    st.dataframe(
                        cur.sort_values(["season_end", "gender", "scope"])[
                            ["season_label", "gender", "scope", "name", "school", "category"]
                        ],
                        use_container_width=True,
                    )
                st.stop()

        # ---- Default champions query path ----
        result = (
            df
            if filters is None
            else df[
                ((df["gender"] == filters.get("gender")) | (filters.get("gender") is None))
                & ((df["event"] == filters.get("event")) | (filters.get("event") is None))
                & ((df["meet"] == filters.get("meet")) | (filters.get("meet") is None))
                & ((df["year"] == filters.get("year")) | (filters.get("year") is None))
            ]
        )
        if filters.get("name"):
            needle = filters["name"].lower()
            result = result[
                result["name"].str.lower().str.contains(needle, na=False)
                | result["school"].str.lower().str.contains(needle, na=False)
            ]
        if result.empty:
            st.error("No matches found. Try adding gender, meet, or year.")
            with st.expander("Detected filters"):
                st.json(filters)
        else:
            if filters.get("year") and filters.get("meet") and filters.get("event") and result.shape[0] == 1:
                row = result.iloc[0]
                st.success(
                    f"**{row['gender'].title()} {row['event']} — {row['meet']} {row['year']}**\n\n"
                    f"🏅 **{row['name']}** ({row['class']}) — {row['school']} — **{row['mark']}**"
                )
            st.dataframe(
                result[["gender", "event", "meet", "year", "name", "class", "school", "mark"]]
                .sort_values(["gender", "event", "meet", "year"], ascending=[True, True, True, False])
                .reset_index(drop=True),
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
        if g != "(any)":
            cur = cur[cur["gender"] == g]
        if m != "(any)":
            cur = cur[cur["meet"] == m]
        if ev != "(any)":
            cur = cur[cur["event"] == ev]
        if y != "(any)":
            cur = cur[cur["year"] == y]
        if who:
            needle = who.lower()
            cur = cur[
                cur["name"].str.lower().str.contains(needle, na=False)
                | cur["school"].str.lower().str.contains(needle, na=False)
            ]

        st.metric("Matching champions", f"{len(cur):,}")
        st.dataframe(
            cur.sort_values(["gender", "event", "meet", "year"], ascending=[True, True, True, False])
               .reset_index(drop=True),
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
        athlete = st.selectbox(
            "Choose athlete",
            options=["(type to search)"] + idx["name"].unique().tolist(),
            index=0,
            help="Start typing a name to filter the list.",
        )
        scope = st.radio(
            "Scope",
            options=["State (Indoor + Division I/II)", "Indoor only", "Outdoor only", "All meets"],
            horizontal=True,
        )

        if athlete and athlete != "(type to search)":
            if scope == "Indoor only":
                include_meets = STATE_MEETS_INDOOR
                scope_label = "Indoor State Championship"
            elif scope == "Outdoor only":
                include_meets = STATE_MEETS_OUTDOOR
                scope_label = "Outdoor (Division I & II)"
            elif scope == "All meets":
                include_meets = set(df["meet"].unique())
                scope_label = "All meets"
            else:
                include_meets = STATE_MEETS_ALL
                scope_label = "State (Indoor + Division I/II)"

            genders = guess_gender_for_name(df, athlete)
            collected = []
            for g in genders:
                count, rows = title_count(
                    df[df["gender"] == g],
                    athlete,
                    include_meets=include_meets,
                    include_relays=False,  # relays excluded
                )
                collected.append((g, count, rows))

            st.markdown(f"### {athlete} — {scope_label}")
            cols = st.columns(len(collected) if collected else 1)
            for i, (g, count, rows) in enumerate(collected or []):
                cols[i].metric(f"{g.title()} titles", int(count))

            any_titles = any(c for _, c, _ in collected)
            if any_titles:
                all_rows = pd.concat([r for _, c, r in collected if c > 0], ignore_index=True)
                if not all_rows.empty:
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        st.caption("By meet")
                        st.dataframe(all_rows.groupby("meet").size().reset_index(name="titles"))
                    with c2:
                        st.caption("By event")
                        st.dataframe(all_rows.groupby("event").size().reset_index(name="titles"))
                    with c3:
                        st.caption("By year")
                        st.dataframe(all_rows.groupby("year").size().reset_index(name="titles").sort_values("year"))
                    st.caption("Title rows (relays excluded)")
                    st.dataframe(
                        all_rows[["gender", "year", "meet", "event", "class", "school", "mark"]],
                        use_container_width=True
                    )
            else:
                st.info("No titles found for the selected scope.")

# ----------------------------
# MVPs
# ----------------------------
with tab4:
    st.subheader("MVPs — Indoor / Outdoor / Cross Country (from MVPs sheet)")
    if mvps_df is None or mvps_df.empty:
        st.info("No MVP data parsed. Ensure the 'MVPs' sheet exists and follows the expected two-row pattern (name then school).")
    else:
        c1, c2, c3 = st.columns(3)
        scope_pick = c1.selectbox("Scope", options=["Indoor", "Outdoor", "Cross Country"])
        gender_pick = c2.selectbox("Gender", options=["GIRLS", "BOYS"])
        since_year = c3.number_input("Since year (end year)", min_value=2000, max_value=2100, value=2010, step=1)

        cur = mvps_df[(mvps_df["scope"] == scope_pick) & (mvps_df["gender"] == gender_pick)]
        cur = cur[cur["season_end"] >= since_year].sort_values(["season_end"])
        st.metric("MVP entries", len(cur))
        st.dataframe(cur[["season_label", "gender", "scope", "name", "school"]], use_container_width=True)

# ----------------------------
# Data Status
# ----------------------------
with tab5:
    st.subheader("Data status / debug")
    if df is None:
        st.info("No data loaded.")
    else:
        try:
            c1, c2, c3, c4 = st.columns(4)
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
