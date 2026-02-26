# streamlit_app.py
# ---------------------------------------------------------
# Delaware HS T&F Champions Q&A — Streamlit
# Loads your “Delaware Track and Field Supersheet (6).xlsx”
# and answers natural-language queries + provides filters.
#
# How it works:
#   • Detect year columns on row 1 (each 4-col bundle: name, class, school, mark)
#   • Walk each event block (col A) + meet rows (col E)
#   • Build normalized rows per gender/event/meet/year
#
# Assumptions (from your workbook’s structure):
#   • GIRLS and BOYS sheets use the same layout
#   • Row 1 contains years across columns, every 4 columns
#   • Column A holds event names, Column E holds meet names
#
# (C) You. Feel free to adapt/extend for newsroom use.

import io
import re
import difflib
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
import openpyxl

# ----------------------------
# Constants & synonym helpers
# ----------------------------
MEETS = {
    "Division I": {"division 1", "div i", "d1"},
    "Division II": {"division 2", "div ii", "d2"},
    "Meet of Champions": {"moc", "meet of champs", "meet of champion"},
    "New Castle County": {"ncc", "new castle", "new castle co"},
    "Henlopen Conference": {"henlopen"},
    "Indoor State Championship": {"indoor", "indoor state", "state indoor", "indoor championship"},
}
# invert synonyms for easier lookups
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
# Parsing the Excel workbook
# ----------------------------
def detect_year_bundles(ws) -> List[Tuple[int, int]]:
    """Return a list of (year, start_col) for each 4-col bundle on row 1."""
    bundles = []
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
    """From column A value → canonical event string."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # e.g., 200.0 → "200"
        s = str(int(raw)) if float(raw).is_integer() else str(raw)
        return s.replace(".0", "")
    s = str(raw).strip()
    # Often workbook stores these as 100/55, 100/55H etc.
    low = s.lower()
    # Match direct
    if low in EVENT_CANONICAL:
        return EVENT_CANONICAL[low]
    # Handle trailing ".0"
    if low.endswith(".0") and low[:-2] in EVENT_CANONICAL:
        return EVENT_CANONICAL[low[:-2]]
    # If this looks like a clean number (e.g., "200"), keep it
    if re.fullmatch(r"\d{2,4}", s):
        return s
    return s


def parse_champions_sheet(ws, gender: str) -> pd.DataFrame:
    """Parse one sheet (GIRLS or BOYS) to normalized champions data."""
    year_bundles = detect_year_bundles(ws)
    records = []

    # Walk rows; event label sits in column A; meet label sits in column E
    current_event = None
    for r in range(1, ws.max_row + 1):
        ev_raw = ws.cell(row=r, column=1).value
        if ev_raw:
            maybe_ev = normalize_event_label(ev_raw)
            # Event rows are placed at regular intervals in col A
            # We treat any non-empty here as a likely event marker.
            if maybe_ev:
                current_event = maybe_ev

        meet_raw = ws.cell(row=r, column=5).value  # col E
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

    df = pd.DataFrame.from_records(records)
    return df


@st.cache_data(show_spinner=False)
def load_and_parse(file_bytes: bytes) -> pd.DataFrame:
    """Load the Excel workbook from bytes and parse both GIRLS & BOYS."""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    girls_df = parse_champions_sheet(wb["GIRLS"], "GIRLS")
    boys_df = parse_champions_sheet(wb["BOYS"], "BOYS")
    out = pd.concat([girls_df, boys_df], ignore_index=True)
    # Clean a few common quirks
    out["event"] = out["event"].astype(str).str.replace(r"\.0$", "", regex=True)
    return out


# ----------------------------
# Natural-language query parsing
# ----------------------------
def canonical_meet(token: str) -> Optional[str]:
    low = token.strip().lower()
    if low in MEET_CANONICAL:
        return MEET_CANONICAL[low]
    # try fuzzy
    choice = difflib.get_close_matches(low, list(MEET_CANONICAL.keys()), n=1, cutoff=0.85)
    if choice:
        return MEET_CANONICAL[choice[0]]
    return None


def canonical_event(event_text: str, gender: Optional[str]) -> Optional[str]:
    """
    Resolve an event string to workbook canonical.
    Special handling:
      • If user says '55' for indoor sprint: map to '100/55'
      • '55 hurdles' → GIRLS '100/55H', BOYS '110/55H'
    """
    t = event_text.strip().lower()
    # direct dictionary mapping
    if t in EVENT_CANONICAL:
        return EVENT_CANONICAL[t]

    # Hurdles ambiguity
    if "hurd" in t or t in {"55h", "110h", "100h"}:
        if gender == "GIRLS":
            return "100/55H"
        if gender == "BOYS":
            return "110/55H"
        # fallback: prefer girls mapping for '55h' if gender unknown
        if "55" in t:
            return "100/55H"

    # 55 dash ambiguity
    if t in {"55", "55m"}:
        return "100/55"

    # mile / two-mile, etc. handled in dictionary above

    # fuzzy last resort against canonical keys
    canon_keys = list({v.lower(): v for v in EVENT_CANONICAL.values()}.keys())
    m = difflib.get_close_matches(t, canon_keys, n=1, cutoff=0.8)
    if m:
        return EVENT_CANONICAL[m[0]]
    return None


def parse_question(q: str) -> Dict[str, Optional[str]]:
    """
    Extract coarse filters:
      • gender
      • event
      • meet
      • year
      • name/school (if present), used for athlete-focused queries
    """
    out = {"gender": None, "event": None, "meet": None, "year": None, "name": None, "school": None}
    t = q.strip()

    # year
    y = re.findall(r"(20\\d{2})", t)
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

    # meet (scan words/phrases)
    # check multi-word first
    lowered = t.lower()
    for phrase in sorted(MEET_CANONICAL.keys(), key=len, reverse=True):
        if phrase in lowered:
            out["meet"] = MEET_CANONICAL[phrase]
            break

    # event: try typical tokens ('200', 'mile', 'long jump', etc.)
    # search longer phrases first
    for ev_phrase in sorted(EVENT_CANONICAL.keys(), key=len, reverse=True):
        if ev_phrase in lowered:
            out["event"] = EVENT_CANONICAL[ev_phrase]
            break

    # if still none, try simple numerics as event candidates
    if not out["event"]:
        nums = re.findall(r"\\b(\\d{2,4})\\b", t)
        if nums:
            out["event"] = canonical_event(nums[0], out["gender"])

    # athlete or school hint (simple heuristic: quoted string or “by <name>”)
    m = re.search(r'\"([^\"]+)\"', t)
    if m:
        out["name"] = m.group(1).strip()

    # if contains common connecting phrase
    m2 = re.search(r"(?:by|from|at)\\s+([A-Z][A-Za-z\\-']+(?:\\s+[A-Z][A-Za-z\\-']+)*)", q)
    if m2 and not out["name"]:
        out["name"] = m2.group(1).strip()

    return out


def apply_filters(df: pd.DataFrame, f: Dict[str, Optional[str]]) -> pd.DataFrame:
    cur = df
    if f.get("gender"):
        cur = cur[cur["gender"] == f["gender"]]
    if f.get("event"):
        # Handle boys/girls hurdles disambiguation (same sheet uses 100/55H for girls, 110/55H for boys)
        ev = f["event"]
        if ev in {"100/55H", "110/55H"} and f.get("gender") is None:
            # if gender unknown, accept both hurdles labels
            cur = cur[cur["event"].isin(["100/55H", "110/55H"])]
        else:
            cur = cur[cur["event"] == ev]
    if f.get("meet"):
        cur = cur[cur["meet"] == f["meet"]]
    if f.get("year"):
        cur = cur[cur["year"] == f["year"]]
    if f.get("name"):
        # fuzzy match on name OR school
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

with st.sidebar:
    st.header("Load data")
    data_source = st.radio(
        "Choose source",
        options=["Use packaged file (recommended)", "Upload your own workbook"],
        index=0,
    )

    default_bytes = None
    if data_source == "Upload your own workbook":
        upl = st.file_uploader("Upload Excel (.xlsx) with GIRLS / BOYS sheets", type=["xlsx"])
        if upl:
            default_bytes = upl.read()
    else:
        # packaged file: place the Excel next to this script and read it
        try:
            with open("Delaware Track and Field Supersheet (6).xlsx", "rb") as f:
                default_bytes = f.read()
        except FileNotFoundError:
            st.warning("Packaged file not found. Upload your workbook instead.")

    if default_bytes:
        df = load_and_parse(default_bytes)
        st.success(f"Loaded champions: {len(df):,} rows")
        st.download_button(
            label="Download normalized champions (CSV)",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="de_champions_normalized.csv",
            mime="text/csv",
        )
    else:
        df = None

tab1, tab2 = st.tabs(["🔎 Ask a question", "🎛️ Explore"])

with tab1:
    st.subheader("Natural-language Q&A")
    st.caption("Examples: “Who won the girls 200 at Indoor in 2026?”, “Show Juliana Balon’s indoor titles”, “Boys long jump MOC 2024”")
    q = st.text_input("Type your question")
    if q and df is not None:
        filters = parse_question(q)
        # Auto-infer meet if user said “55” and also typed “indoor”
        if (filters.get("event") in {"100/55", "100/55H", "110/55H"}) and not filters.get("meet"):
            filters["meet"] = "Indoor State Championship"

        result = apply_filters(df, filters)
        # When overly broad, summarize first
        if result.empty:
            st.error("No matches found. Try adding a meet, year, or gender.")
            # show a small hint
            st.write("Detected filters:", filters)
        else:
            # If the query resolves to a single champion, present as a card
            if (filters.get("year") and filters.get("meet") and filters.get("event") and result.shape[0] == 1):
                row = result.iloc[0]
                st.success(
                    f"**{row['gender'].title()} {row['event']} — {row['meet']} {row['year']}**\n\n"
                    f"🏅 **{row['name']}** ({row['class']}) — {row['school']} — **{row['mark']}**"
                )
            # Otherwise show a tidy table
            st.dataframe(
                result[["gender", "event", "meet", "year", "name", "class", "school", "mark"]]
                .sort_values(["gender", "event", "meet", "year"], ascending=[True, True, True, False])
                .reset_index(drop=True),
                use_container_width=True,
            )

with tab2:
    st.subheader("Filter champions")
    if df is None:
        st.info("Load a workbook in the sidebar to begin.")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        g = c1.selectbox("Gender", options=["(any)"] + sorted(df["gender"].unique().tolist()))
        m = c2.selectbox("Meet", options=["(any)"] + sorted(df["meet"].unique().tolist()))
        # event picker with friendly names
        ev_all = sorted(df["event"].unique().tolist(), key=lambda x: (x in {"LJ","TJ","HJ","PV"}, x))
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
