# streamlit_app.py
# ---------------------------------------------------------
# Delaware HS Track & Field Champions Q&A — Streamlit
# Bundled workbook version (no upload required).
#
# How it works:
#   • Detects year bundles on row 1: [Year][Name][Class][School][Mark] in 4-col groups
#   • Reads event labels from column A and meet labels from column E
#   • Normalizes GIRLS + BOYS into one table: gender, event, meet, year, name, class, school, mark
#
# Notes:
#   • Place "Delaware Track and Field Supersheet (6).xlsx" in the same folder as this file.
#   • The parser expects GIRLS and BOYS sheets with the layout described above.

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
MEETS = {
    "Division I": {"division 1", "div i", "d1"},
    "Division II": {"division 2", "div ii", "d2"},
    "Meet of Champions": {"moc", "meet of champs", "meet of champion"},
    "New Castle County": {"ncc", "new castle", "new castle co"},
    "Henlopen Conference": {"henlopen"},
    "Indoor State Championship": {"indoor", "indoor state", "state indoor", "indoor championship"},
}
MEET_CANONICAL = {}
for k, vals in MEETS.items():
    MEET_CANONICAL[k.lower()] = k
    for v in vals:
        MEET_CANONICAL[v.lower()] = k

EVENTS = {
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
EVENT_CANONICAL = {}
for k, vals in EVENTS.items():
    EVENT_CANONICAL[k.lower()] = k
    for v in vals:
        EVENT_CANONICAL[v.lower()] = k

GENDER_ALIASES = {
    "girls": {"girls", "girl", "g", "women", "female"},
    "boys": {"boys", "boy", "b", "men", "male"},
}
GENDER_CANONICAL = {}
for k, vals in GENDER_ALIASES.items():
    GENDER_CANONICAL[k] = k.upper()
    for v in vals:
        GENDER_CANONICAL[v] = k.upper()


# ----------------------------
# Workbook parsing helpers
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
def load_and_parse(file_bytes: bytes) -> pd.DataFrame:
    """Load Excel bytes and parse GIRLS + BOYS sheets into a single DataFrame."""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    girls_df = parse_champions_sheet(wb["GIRLS"], "GIRLS")
    boys_df = parse_champions_sheet(wb["BOYS"], "BOYS")
    out = pd.concat([girls_df, boys_df], ignore_index=True)
    out["event"] = out["event"].astype(str).str.replace(r"\.0$", "", regex=True)
    return out


# ----------------------------
# Natural-language parsing
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

    if t in {"55", "55m"}:
        return "100/55"

    canon_keys = list({v.lower(): v for v in EVENT_CANONICAL.values()}.keys())
    m = difflib.get_close_matches(t, canon_keys, n=1, cutoff=0.8)
    if m:
        return EVENT_CANONICAL[m[0]]
    return None


def parse_question(q: str) -> Dict[str, Optional[str]]:
    out = {"gender": None, "event": None, "meet": None, "year": None, "name": None, "school": None}
    t = q.strip()

    # year
    y = re.findall(r"(20\d{2})", t)
    if y:
        out["year"] = int(y[0])

    # gender
    gmatch = None
    for tok in re.findall(r"[A-Za-z]+", t):
        low = tok.lower()
        if low in GENDER_CANONICAL:
            gmatch = GENDER_CANONICAL[low]
            break
    out["gender"] = gmatch

    # meet (search phrases)
    lowered = t.lower()
    for phrase in sorted(MEET_CANONICAL.keys(), key=len, reverse=True):
        if phrase in lowered:
            out["meet"] = MEET_CANONICAL[phrase]
            break

    # event
    for ev_phrase in sorted(EVENT_CANONICAL.keys(), key=len, reverse=True):
        if ev_phrase in lowered:
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
        m2 = re.search(r"(?:by|from|at)\s+([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+)*)", q)
        if m2:
            out["name"] = m2.group(1).strip()

    return out


def apply_filters(df: pd.DataFrame, f: Dict[str, Optional[str]]) -> pd.DataFrame:
    cur = df
    if f.get("gender"):
        cur = cur[cur["gender"] == f["gender"]]
    if f.get("event"):
        ev = f["event"]
        if ev in {"100/55H", "110/55H"} and f.get("gender") is None:
            cur = cur[cur["event"].isin(["100/55H", "110/55H"])]
        else:
            cur = cur[cur["event"] == ev]
    if f.get("meet"):
        cur = cur[cur["meet"] == f["meet"]]
    if f.get("year"):
        cur = cur[cur["year"] == f["year"]]
    if f.get("name"):
        needle = f["name"].lower()
        cur = cur[
            cur["name"].str.lower().str.contains(needle, na=False)
            | cur["school"].str.lower().str.contains(needle, na=False)
        ]
    return cur.sort_values(["gender", "event", "meet", "year"])


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="DE HS Track & Field Champions Q&A", page_icon="🏃", layout="wide")
st.title("Delaware HS Track & Field — Champions Q&A")

# ---------- FIX 1: Bundled, path-safe loader (no upload required) ----------
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
    else:
        try:
            with open(BUNDLED_XLSX_PATH, "rb") as f:
                default_bytes = f.read()
            df = load_and_parse(default_bytes)
            st.success(f"Loaded champions: {len(df):,} rows")
            st.caption(f"Workbook: {BUNDLED_XLSX_NAME}")
        except Exception as ex:
            st.error("There was a problem parsing the bundled workbook.")
            st.exception(ex)
            df = None
# ---------------------------------------------------------------------------

tab1, tab2, tab3 = st.tabs(["🔎 Ask a question", "🎛️ Explore", "🛠️ Data status"])

with tab1:
    st.subheader("Natural-language Q&A")
    st.caption("Examples: “Who won the girls 200 at Indoor in 2026?”, “Show Juliana Balon indoor titles”, “Boys long jump MOC 2024”.")
    q = st.text_input("Type your question")
    if q and df is not None:
        filters = parse_question(q)

        # Helpful default: if user asked about '55' dash/hurdles but no meet, assume Indoor
        if (filters.get("event") in {"100/55", "100/55H", "110/55H"}) and not filters.get("meet"):
            filters["meet"] = "Indoor State Championship"

        result = apply_filters(df, filters)
        if result.empty:
            st.error("No matches found. Try adding gender, meet, or year.")
            with st.expander("Detected filters"):
                st.json(filters)
        else:
            if (filters.get("year") and filters.get("meet") and filters.get("event") and result.shape[0] == 1):
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
            cur.sort_values(["gender", "event", "meet", "year"], ascending=[True, True, True, False])
               .reset_index(drop=True),
            use_container_width=True
        )

with tab3:
    st.subheader("Data status / debug")
    if df is None:
        st.info("No data loaded.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total rows", f"{len(df):,}")
        c2.metric("Min year", int(df["year"].min()))
        c3.metric("Max year", int(df["year"].max()))
        st.write("Meets:", sorted(df["meet"].unique()))
        st.write("Events (sample):", sorted(df["event"].unique())[:16])
        st.write("Example rows:")
        st.dataframe(df.head(20), use_container_width=True)
