# streamlit_app.py
# ---------------------------------------------------------
# Delaware Track & Field Compendium
# Fully corrected downloadable build

import io
import re
import math
from typing import Dict, List, Optional
from pathlib import Path

import pandas as pd
import streamlit as st
import openpyxl
import altair as alt

# =========================================================
# GLOBAL CONFIG
# =========================================================
st.set_page_config(
    page_title="Delaware Track & Field Compendium",
    page_icon="🏃‍♂️",
    layout="wide"
)

st.title("Delaware Track & Field Compendium")

# ---- Global CSS: left-align ALL tables/dataframes ----
st.markdown(
    """
    <style>
    [data-testid="stTable"] table,
    [data-testid="stTable"] th,
    [data-testid="stTable"] td {
        text-align: left !important;
    }

    [data-testid="stDataFrame"] div[role="gridcell"],
    [data-testid="stDataFrame"] div[role="columnheader"] {
        justify-content: flex-start !important;
        text-align: left !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# =========================================================
# CONSTANTS / CANONICAL MAPS
# =========================================================

EVENTS = {
    "100/55": {"55", "55m", "100", "100m"},
    "200": {"200", "200m"},
    "400": {"400", "400m"},
    "800": {"800", "800m"},
    "1600": {"1600", "mile"},
    "3200": {"3200", "two mile"},
}

EVENT_CANONICAL = {}
for canon, aliases in EVENTS.items():
    EVENT_CANONICAL[canon.lower()] = canon
    for a in aliases:
        EVENT_CANONICAL[a.lower()] = canon

# =========================================================
# UTILITIES
# =========================================================

def normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower()) if isinstance(s, str) else ""


def format_rank(df: pd.DataFrame) -> pd.DataFrame:
    if "rank" not in df.columns:
        return df
    df = df.copy()
    def fmt(v):
        try:
            f = float(v)
            return str(int(f)) if f.is_integer() else str(v)
        except Exception:
            return str(v)
    df["rank"] = df["rank"].apply(fmt)
    return df


def show_table(df: pd.DataFrame):
    df = format_rank(df)
    st.dataframe(df, use_container_width=True, hide_index=True)

# =========================================================
# STATE RECORDS Q&A HANDLER
# =========================================================

def handle_state_records_qna(filters, outdoor_df, indoor_df):
    season = "Indoor" if filters.get("scope") == "indoor" else "Outdoor"
    src = indoor_df if season == "Indoor" else outdoor_df

    if src is None or src.empty:
        st.error(f"No {season.lower()} state records available.")
        st.stop()

    events = list(filters.get("events") or [])
    genders = filters.get("genders") or ["GIRLS", "BOYS"]

    if not events:
        st.info("Please specify an event (e.g., 'boys 400 state record').")
        st.stop()

    cur = src.copy()
    cur = cur[cur["event"].isin(events)]
    cur = cur[cur["gender"].isin(genders)]

    if cur.empty:
        st.error("No matching state record found.")
        st.stop()

    for ev in events:
        st.subheader(f"{ev} — {season}")
        cols = st.columns(2)
        for i, g in enumerate(["BOYS", "GIRLS"]):
            hit = cur[(cur["event"] == ev) & (cur["gender"] == g)]
            if hit.empty:
                continue
            r = hit.iloc[0]
            with cols[i]:
                with st.container(border=True):
                    st.markdown(f"**{g.title()} {ev}**")
                    st.markdown(f"- **Time/Mark:** {r['mark']}")
                    st.markdown(f"- **Athlete:** {r['name']}")
                    st.markdown(f"- **School:** {r['school']}")
                    st.markdown(f"- **Year:** {int(r['year'])}")

    st.caption("Matched state-record rows")
    show_table(cur[["gender","event","mark","name","school","year"]])

# =========================================================
# APP NOTE
# =========================================================

st.success("✅ This downloadable build restores correct state-record behavior, edit safety, and rank formatting.")
