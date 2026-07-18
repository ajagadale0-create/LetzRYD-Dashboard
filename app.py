"""AI Dashboard — one table: allocation vehicles + Uber + Ola metrics."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.uber_process import (
    available_pt_business_dates,
    build_uber_vehicle_days,
    source_fingerprint,
    uber_root,
)
from lib.uber_allocation import (
    assemble_fleet_table,
    run_on_daily_counts,
    ageing_daily_counts,
    revenue_deadmile_daily,
)
from lib.allocation import (
    allocation_dir,
    list_allocation_files,
    load_allocation,
)
from lib.ola_process import (
    build_ola_vehicle_days,
    ola_dir,
    list_ola_files,
)
from lib.gps_process import (
    build_gps_vehicle_days,
    gps_dir,
    list_gps_files,
)
import plotly.express as px


st.set_page_config(
    page_title="AI Dashboard",
    page_icon="▣",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,600;9..144,700&display=swap');

      html, body, [class*="css"] {
        font-family: 'DM Sans', sans-serif;
      }
      .block-container { padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1280px; }
      h1, h2, h3 {
        font-family: 'Fraunces', Georgia, serif !important;
        letter-spacing: -0.02em;
        color: #12241C !important;
      }
      [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #12352B 0%, #0B241D 100%);
      }
      [data-testid="stSidebar"] * { color: #E8F2ED !important; }
      .brand {
        font-family: 'Fraunces', Georgia, serif;
        font-size: 1.55rem;
        font-weight: 700;
        color: #F0FAF5;
        margin-bottom: 0.15rem;
      }
      .brand-sub { font-size: 0.82rem; opacity: 0.72; margin-bottom: 1.4rem; }
      .section-rule {
        height: 1px;
        background: #D3E0D9;
        margin: 0.4rem 0 1.1rem;
      }
      div[data-testid="stDataFrame"] { border: 1px solid #D3E0D9; border-radius: 12px; overflow: hidden; }
      /* Compact filter row */
      div[data-testid="stHorizontalBlock"] div[data-testid="stSelectbox"] label {
        font-size: 0.72rem !important;
        margin-bottom: 0.05rem !important;
      }
      div[data-testid="stHorizontalBlock"] div[data-testid="stSelectbox"] {
        margin-bottom: 0.2rem !important;
      }
      div[data-testid="stHorizontalBlock"] div[data-baseweb="select"] > div {
        min-height: 2rem !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def _file_sig(path: Path) -> str:
    """Stable-ish signature — size only (Drive mtime flickers and busts cache)."""
    try:
        return f"{path.name}:{path.stat().st_size}"
    except OSError:
        return f"{path.name}:missing"


def current_data_fingerprint() -> str:
    parts = ["v20-deadmile-floor", source_fingerprint(uber_root())]
    for path in list_allocation_files(allocation_dir()):
        parts.append(f"alloc:{_file_sig(path)}")
    for path in list_ola_files():
        parts.append(f"ola:{_file_sig(path)}")
    for path in list_gps_files():
        parts.append(f"gps:{_file_sig(path)}")
    return "|".join(parts)


@st.cache_data(show_spinner="Loading allocation…", ttl=3600)
def cached_allocation(fingerprint: str):
    _ = fingerprint
    return load_allocation()


@st.cache_data(show_spinner="Loading Uber metrics…", ttl=3600)
def cached_uber_days(fingerprint: str):
    _ = fingerprint
    return build_uber_vehicle_days()


@st.cache_data(show_spinner="Loading Ola metrics…", ttl=3600)
def cached_ola_days(fingerprint: str):
    _ = fingerprint
    return build_ola_vehicle_days(ola_dir())


@st.cache_data(show_spinner="Loading GPS metrics…", ttl=3600)
def cached_gps_days(fingerprint: str):
    _ = fingerprint
    return build_gps_vehicle_days(gps_dir())


@st.cache_data(show_spinner="Reading available dates…", ttl=3600)
def load_available_dates(fingerprint: str) -> list[str]:
    _ = fingerprint
    dates: set[str] = set(available_pt_business_dates())
    try:
        alloc, _ = cached_allocation(fingerprint)
        if not alloc.empty:
            dates.update(alloc["Date"].dt.strftime("%Y-%m-%d").dropna().unique().tolist())
    except Exception:
        pass
    try:
        ola_days, _ = cached_ola_days(fingerprint)
        if not ola_days.empty:
            dates.update(
                ola_days["Date"].dt.strftime("%Y-%m-%d").dropna().unique().tolist()
            )
    except Exception:
        pass
    try:
        gps_days, _ = cached_gps_days(fingerprint)
        if not gps_days.empty:
            dates.update(
                gps_days["Date"].dt.strftime("%Y-%m-%d").dropna().unique().tolist()
            )
    except Exception:
        pass
    return sorted(dates)


@st.cache_data(show_spinner="Building Run On trend…")
def load_run_on_trend(fingerprint: str, city: str = "All cities") -> pd.DataFrame:
    uber_days, _ = cached_uber_days(fingerprint)
    ola_days, _ = cached_ola_days(fingerprint)
    alloc, _ = cached_allocation(fingerprint)
    return run_on_daily_counts(
        uber_days,
        ola_days,
        last_n_days=7,
        alloc=alloc,
        city=None if city == "All cities" else city,
    )


@st.cache_data(show_spinner="Building Ageing trend…")
def load_ageing_trend(fingerprint: str, city: str = "All cities") -> pd.DataFrame:
    uber_days, _ = cached_uber_days(fingerprint)
    ola_days, _ = cached_ola_days(fingerprint)
    alloc, _ = cached_allocation(fingerprint)
    return ageing_daily_counts(
        alloc,
        uber_days,
        ola_days,
        last_n_days=7,
        city=None if city == "All cities" else city,
    )


@st.cache_data(show_spinner="Building Revenue / Deadmile trend…")
def load_revenue_deadmile_trend(
    fingerprint: str, city: str = "All cities"
) -> pd.DataFrame:
    uber_days, _ = cached_uber_days(fingerprint)
    ola_days, _ = cached_ola_days(fingerprint)
    gps_days, _ = cached_gps_days(fingerprint)
    alloc, _ = cached_allocation(fingerprint)
    return revenue_deadmile_daily(
        alloc,
        uber_days,
        ola_days,
        gps_days,
        last_n_days=7,
        city=None if city == "All cities" else city,
    )


@st.cache_data(show_spinner="Building table…")
def load_range_table(start_date: str, end_date: str, fingerprint: str):
    alloc, alloc_meta = cached_allocation(fingerprint)
    uber_days, _ = cached_uber_days(fingerprint)
    ola_days, _ = cached_ola_days(fingerprint)
    gps_days, _ = cached_gps_days(fingerprint)
    return assemble_fleet_table(
        start_date,
        end_date,
        alloc,
        alloc_meta,
        uber_days,
        ola_days,
        gps_days,
        write_output=False,
    )


def _line_chart_with_labels(
    trend: pd.DataFrame,
    *,
    color_col: str,
    category_orders: dict,
    color_map: dict,
    title: str,
    caption: str,
) -> None:
    if trend.empty:
        return
    st.subheader(title)
    st.caption(caption)

    plot_df = trend.copy()
    # Hide 0 labels so they don't clutter; keep marker
    plot_df["_label"] = plot_df["Vehicles"].map(lambda v: "" if int(v) == 0 else str(int(v)))

    fig = px.line(
        plot_df,
        x="Date",
        y="Vehicles",
        color=color_col,
        markers=True,
        text="_label",
        category_orders=category_orders,
        color_discrete_map=color_map,
        labels={"Vehicles": "Vehicles", "Date": "Date", color_col: color_col},
    )

    # Label by line height: top & mid → above; lowest line → side
    def _trace_mean(trace) -> float:
        ys = trace.y
        if ys is None:
            return 0.0
        vals = []
        for y in ys:
            if y is None:
                continue
            try:
                vals.append(float(y))
            except (TypeError, ValueError):
                continue
        return sum(vals) / len(vals) if vals else 0.0

    ranked = sorted(
        enumerate(fig.data),
        key=lambda it: _trace_mean(it[1]),
        reverse=True,
    )
    for rank, (_, trace) in enumerate(ranked):
        if rank <= 1:
            trace.textposition = "top center"  # top + mid lines: above
        else:
            trace.textposition = "bottom center"  # last (lowest) line: below
        trace.cliponaxis = False
        trace.textfont = dict(size=12, family="DM Sans", color="#0B1F18")
        trace.marker = dict(size=8)
        trace.texttemplate = "%{text}"

    y_max = float(plot_df["Vehicles"].max()) if len(plot_df) else 0
    fig.update_layout(
        height=320,
        margin=dict(l=10, r=20, t=50, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="DM Sans", color="#1A2B24", size=11),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, x=0, font=dict(size=10)),
        xaxis=dict(gridcolor="#DCE6E1", tickangle=-30),
        yaxis=dict(
            gridcolor="#DCE6E1",
            title="Vehicles",
            range=[0, y_max * 1.25 + 5],
        ),
        uniformtext_minsize=10,
        uniformtext_mode="show",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_run_on_chart(trend: pd.DataFrame) -> None:
    _line_chart_with_labels(
        trend,
        color_col="Run On",
        category_orders={"Run On": ["OLA", "Uber", "OLA+Uber"]},
        color_map={
            "OLA": "#0F6E56",
            "Uber": "#1B4F9C",
            "OLA+Uber": "#C45C26",
        },
        title="Run On",
        caption="Last 7 days · OLA / Uber / OLA+Uber",
    )


def _render_ageing_chart(trend: pd.DataFrame) -> None:
    _line_chart_with_labels(
        trend,
        color_col="Ageing",
        category_orders={"Ageing": ["< 2500", "> 2500", "Not running"]},
        color_map={
            "< 2500": "#C45C26",
            "> 2500": "#0F6E56",
            "Not running": "#111111",
        },
        title="Ageing",
        caption="Last 7 days · unique on-road vehicles · < 2500 / > 2500 / Not running",
    )


def _render_revenue_deadmile_chart(trend: pd.DataFrame) -> None:
    if trend.empty:
        return
    st.subheader("Revenue & Deadmile Charges")
    st.caption(
        "Last 7 days · Total Revenue · Deadmile Charges by type (Unproductive / Deadmile)"
    )

    plot_df = trend.copy()
    plot_df["_label"] = plot_df["Amount"].map(
        lambda v: "" if abs(float(v)) < 0.5 else f"{float(v):,.0f}"
    )

    fig = px.line(
        plot_df,
        x="Date",
        y="Amount",
        color="Metric",
        markers=True,
        text="_label",
        category_orders={
            "Metric": ["Total Revenue", "Unproductive", "Deadmile"]
        },
        color_discrete_map={
            "Total Revenue": "#0F6E56",
            "Unproductive": "#C45C26",
            "Deadmile": "#1B4F9C",
        },
        labels={"Amount": "Amount", "Date": "Date", "Metric": "Metric"},
    )

    def _trace_mean(trace) -> float:
        ys = trace.y
        if ys is None:
            return 0.0
        vals = []
        for y in ys:
            if y is None:
                continue
            try:
                vals.append(float(y))
            except (TypeError, ValueError):
                continue
        return sum(vals) / len(vals) if vals else 0.0

    ranked = sorted(
        enumerate(fig.data),
        key=lambda it: _trace_mean(it[1]),
        reverse=True,
    )
    for rank, (_, trace) in enumerate(ranked):
        if rank <= 1:
            trace.textposition = "top center"
        else:
            trace.textposition = "bottom center"
        trace.cliponaxis = False
        trace.textfont = dict(size=11, family="DM Sans", color="#0B1F18")
        trace.marker = dict(size=8)
        trace.texttemplate = "%{text}"

    y_max = float(plot_df["Amount"].max()) if len(plot_df) else 0
    fig.update_layout(
        height=340,
        margin=dict(l=10, r=20, t=50, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="DM Sans", color="#1A2B24", size=11),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, x=0, font=dict(size=10)),
        xaxis=dict(gridcolor="#DCE6E1", tickangle=-30),
        yaxis=dict(
            gridcolor="#DCE6E1",
            title="Amount",
            range=[0, y_max * 1.25 + 5],
        ),
        uniformtext_minsize=9,
        uniformtext_mode="show",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_filtered_views(
    table: pd.DataFrame, partner: str, type_pick: str, city: str
) -> None:
    summary_base = table
    if city != "All cities":
        summary_base = summary_base[
            summary_base["City"].fillna("").astype(str).str.strip() == city
        ]
    if partner != "All partners":
        summary_base = summary_base[
            summary_base["Partner Name"].fillna("").astype(str).str.strip() == partner
        ]

    type_label = summary_base["Type"].fillna("").astype(str).str.strip()
    type_label = type_label.where(type_label.isin(["Operator", "Individual"]), "Other")
    type_summary = (
        summary_base.assign(_Type=type_label)
        .groupby("_Type", as_index=False)
        .agg(
            **{
                "Vehicle Count": ("Vehicle Number", "nunique"),
                "Total Revenue": ("Total Revenue", "sum"),
                "Total Cash Collection": ("Total Cash Collection", "sum"),
                "Total Trip": ("Total Trip", "sum"),
                "Revenue": ("Revenue", "sum"),
                "Cash Collection": ("Cash Collection", "sum"),
                "Ola Customer Bill": ("Ola Customer Bill", "sum"),
                "Ola Cash Collected": ("Ola Cash Collected", "sum"),
            }
        )
        .rename(columns={"_Type": "Type"})
    )
    order = {"Operator": 0, "Individual": 1, "Other": 2}
    type_summary["_ord"] = type_summary["Type"].map(lambda t: order.get(t, 9))
    type_summary = type_summary.sort_values("_ord").drop(columns=["_ord"])

    total_row = pd.DataFrame(
        [
            {
                "Type": "Total",
                "Vehicle Count": int(summary_base["Vehicle Number"].nunique()),
                "Total Revenue": float(summary_base["Total Revenue"].sum()),
                "Total Cash Collection": float(
                    summary_base["Total Cash Collection"].sum()
                ),
                "Total Trip": int(summary_base["Total Trip"].sum()),
                "Revenue": float(summary_base["Revenue"].sum()),
                "Cash Collection": float(summary_base["Cash Collection"].sum()),
                "Ola Customer Bill": float(summary_base["Ola Customer Bill"].sum()),
                "Ola Cash Collected": float(summary_base["Ola Cash Collected"].sum()),
            }
        ]
    )
    type_summary = pd.concat([type_summary, total_row], ignore_index=True)
    for col in (
        "Total Revenue",
        "Total Cash Collection",
        "Revenue",
        "Cash Collection",
        "Ola Customer Bill",
        "Ola Cash Collected",
    ):
        type_summary[col] = type_summary[col].round(2)
    type_summary["Total Trip"] = type_summary["Total Trip"].astype(int)
    type_summary["Vehicle Count"] = type_summary["Vehicle Count"].astype(int)

    # Ageing summary (same filter base as type summary)
    ageing_base = summary_base.copy()
    if type_pick != "All types":
        ageing_base = ageing_base[
            ageing_base["Type"].fillna("").astype(str).str.strip() == type_pick
        ]
    ageing_label = ageing_base["Ageing"].fillna("").astype(str).str.strip()
    ageing_label = ageing_label.where(
        ageing_label.isin(["< 2500", "> 2500", "Not running"]), "Other"
    )
    ageing_summary = (
        ageing_base.assign(_Ageing=ageing_label)
        .groupby("_Ageing", as_index=False)
        .agg(
            **{
                "Vehicle Count": ("Vehicle Number", "nunique"),
                "Total Revenue": ("Total Revenue", "sum"),
                "Total Cash Collection": ("Total Cash Collection", "sum"),
                "Total Trip": ("Total Trip", "sum"),
            }
        )
        .rename(columns={"_Ageing": "Ageing"})
    )
    ageing_order = {"< 2500": 0, "> 2500": 1, "Not running": 2, "Other": 3}
    ageing_summary["_ord"] = ageing_summary["Ageing"].map(
        lambda a: ageing_order.get(a, 9)
    )
    ageing_summary = ageing_summary.sort_values("_ord").drop(columns=["_ord"])
    ageing_total = pd.DataFrame(
        [
            {
                "Ageing": "Total",
                "Vehicle Count": int(ageing_base["Vehicle Number"].nunique())
                if len(ageing_base)
                else 0,
                "Total Revenue": float(ageing_base["Total Revenue"].sum())
                if len(ageing_base)
                else 0.0,
                "Total Cash Collection": float(
                    ageing_base["Total Cash Collection"].sum()
                )
                if len(ageing_base)
                else 0.0,
                "Total Trip": int(ageing_base["Total Trip"].sum())
                if len(ageing_base)
                else 0,
            }
        ]
    )
    ageing_summary = pd.concat([ageing_summary, ageing_total], ignore_index=True)
    ageing_summary["Total Revenue"] = ageing_summary["Total Revenue"].round(2)
    ageing_summary["Total Cash Collection"] = ageing_summary[
        "Total Cash Collection"
    ].round(2)
    ageing_summary["Total Trip"] = ageing_summary["Total Trip"].astype(int)
    ageing_summary["Vehicle Count"] = ageing_summary["Vehicle Count"].astype(int)

    money_cfg = {
        "Total Revenue": st.column_config.NumberColumn(format="₹%.2f"),
        "Total Cash Collection": st.column_config.NumberColumn(format="₹%.2f"),
        "Revenue": st.column_config.NumberColumn(format="₹%.2f"),
        "Cash Collection": st.column_config.NumberColumn(format="₹%.2f"),
        "Ola Customer Bill": st.column_config.NumberColumn(format="₹%.2f"),
        "Ola Cash Collected": st.column_config.NumberColumn(format="₹%.2f"),
    }

    s1, s2 = st.columns(2)
    with s1:
        st.subheader("Type summary")
        st.dataframe(
            type_summary,
            use_container_width=True,
            hide_index=True,
            height=200,
            column_config=money_cfg,
        )
    with s2:
        st.subheader("Ageing summary")
        st.dataframe(
            ageing_summary,
            use_container_width=True,
            hide_index=True,
            height=200,
            column_config={
                "Total Revenue": st.column_config.NumberColumn(format="₹%.2f"),
                "Total Cash Collection": st.column_config.NumberColumn(format="₹%.2f"),
            },
        )

    view = table
    if city != "All cities":
        view = view[view["City"].fillna("").astype(str).str.strip() == city]
    if partner != "All partners":
        view = view[view["Partner Name"].fillna("").astype(str).str.strip() == partner]
    if type_pick != "All types":
        view = view[view["Type"].fillna("").astype(str).str.strip() == type_pick]

    if "Total Revenue" in view.columns and not view.empty:
        view = view.sort_values(
            ["Total Revenue", "Partner Name", "Vehicle Number"],
            ascending=[False, True, True],
        ).reset_index(drop=True)

    st.subheader("Detail")
    st.caption(
        "Totals first · Total Revenue = Uber + Ola bill · "
        "Total Cash = Uber + Ola cash · Total Trip = Uber + Ola trips · "
        "Total Intrip KM = Uber Trip Distance + Ola Actual Kms · "
        "Ideal KM = (30 × days) + (3 × trips) + Intrip · "
        "Buffer KM = GPS − Ideal · Deadmile Charges = max(0, Buffer) × 3 · "
        "Dead KM = max(0, GPS − Ideal) · Type = Unproductive / Deadmile"
    )
    st.dataframe(
        view,
        use_container_width=True,
        height=560,
        hide_index=True,
        column_config={
            "Total Revenue": st.column_config.NumberColumn(
                "Total Revenue", format="₹%.2f"
            ),
            "Total Cash Collection": st.column_config.NumberColumn(
                "Total Cash Collection", format="₹%.2f"
            ),
            "Total Trip": st.column_config.NumberColumn("Total Trip"),
            "Total Intrip KM": st.column_config.NumberColumn(
                "Total Intrip KM", format="%.2f"
            ),
            "GPS KMs": st.column_config.NumberColumn("GPS KMs", format="%.2f"),
            "Ideal KM": st.column_config.NumberColumn("Ideal KM", format="%.2f"),
            "Buffer KM": st.column_config.NumberColumn("Buffer KM", format="%.2f"),
            "Deadmile Charges": st.column_config.NumberColumn("Deadmile Charges", format="%.2f"),
            "Dead KM": st.column_config.NumberColumn("Dead KM", format="%.2f"),
            "Dead KM Type": st.column_config.TextColumn("Dead KM Type"),
            "Run On": st.column_config.TextColumn("Run On"),
            "Ageing": st.column_config.TextColumn("Ageing"),
            "Revenue": st.column_config.NumberColumn("Uber Revenue", format="₹%.2f"),
            "Cash Collection": st.column_config.NumberColumn(
                "Uber Cash", format="₹%.2f"
            ),
            "Ola Customer Bill": st.column_config.NumberColumn(
                "Ola Customer Bill", format="₹%.2f"
            ),
            "Ola Cash Collected": st.column_config.NumberColumn(
                "Ola Cash Collected", format="₹%.2f"
            ),
            "Trip Distance": st.column_config.NumberColumn(
                "Uber Trip Distance", format="%.2f"
            ),
            "Ola Actual Kms": st.column_config.NumberColumn(
                "Ola Actual Kms", format="%.2f"
            ),
            "GPS KMs": st.column_config.NumberColumn("GPS KMs", format="%.2f"),
            "Ola Trip Time": st.column_config.NumberColumn("Ola Trip Time"),
            "Trip Completed Count": st.column_config.NumberColumn("Uber Trips"),
            "Ola Trips": st.column_config.NumberColumn("Ola Trips"),
        },
    )


def main():
    # Poll less often — Drive file checks are slow
    @st.fragment(run_every=60)
    def _watch_folders():
        latest = current_data_fingerprint()
        prev = st.session_state.get("seen_fingerprint")
        if prev is None:
            st.session_state.seen_fingerprint = latest
            return
        if latest and latest != prev:
            st.session_state.seen_fingerprint = latest
            st.cache_data.clear()
            st.rerun()

    _watch_folders()

    fp = current_data_fingerprint()
    try:
        available_dates = load_available_dates(fp)
    except Exception:
        available_dates = []

    with st.sidebar:
        st.markdown('<div class="brand">AI Dashboard</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="brand-sub">Allocation vehicles + Uber + Ola</div>',
            unsafe_allow_html=True,
        )
        if st.button("Refresh data now", use_container_width=True):
            st.cache_data.clear()
            for k in (
                "day_start",
                "day_end",
                "day_partner",
                "day_type",
                "day_city",
                "seen_fingerprint",
            ):
                st.session_state.pop(k, None)
            st.rerun()

    st.title("Fleet day table")
    st.caption(
        "Uber date = Drop-off 4am→4am (file 15–16 → day 15) · Ola = calendar date · "
        "First load slow once, then cached"
    )

    if not available_dates:
        st.error("No dates found. Add Uber PT/Trip CSVs and allocation Excel.")
        return

    default_end = available_dates[-1]
    default_start = available_dates[max(0, len(available_dates) - 10)]

    for key, default in (("day_start", default_start), ("day_end", default_end)):
        if key in st.session_state and st.session_state[key] not in available_dates:
            del st.session_state[key]
        if key not in st.session_state:
            st.session_state[key] = default

    try:
        alloc_preview, _ = cached_allocation(fp)
        cities = sorted(
            {
                str(c).strip()
                for c in alloc_preview.get("City", pd.Series(dtype=str)).fillna("").tolist()
                if str(c).strip() and str(c).strip().lower() not in {"nan", "none", "-"}
            }
        )
    except Exception:
        cities = []
    city_options = ["All cities"] + cities
    if "day_city" in st.session_state and st.session_state["day_city"] not in city_options:
        del st.session_state["day_city"]
    if "day_city" not in st.session_state:
        st.session_state["day_city"] = "All cities"

    start_date = st.session_state["day_start"]
    end_date = st.session_state["day_end"]
    if end_date < start_date:
        start_date, end_date = end_date, start_date
        st.session_state["day_start"] = start_date
        st.session_state["day_end"] = end_date

    try:
        table, _meta = load_range_table(start_date, end_date, fp)
    except Exception as exc:
        st.error(f"Could not build table: {exc}")
        return

    city_sel = st.session_state.get("day_city", "All cities")
    table_for_opts = table
    if city_sel != "All cities" and "City" in table.columns:
        table_for_opts = table[
            table["City"].fillna("").astype(str).str.strip() == city_sel
        ]

    partners = sorted(
        {
            str(p).strip()
            for p in table_for_opts.get("Partner Name", pd.Series(dtype=str))
            .fillna("")
            .tolist()
            if str(p).strip()
        }
    )
    partner_options = ["All partners"] + partners
    if "day_partner" in st.session_state and st.session_state["day_partner"] not in partner_options:
        st.session_state["day_partner"] = "All partners"
    if "day_partner" not in st.session_state:
        st.session_state["day_partner"] = "All partners"

    type_values = sorted(
        {
            str(t).strip()
            for t in table_for_opts.get("Type", pd.Series(dtype=str)).fillna("").tolist()
            if str(t).strip() and str(t).strip() not in {"-", "nan", "None"}
        }
    )
    preferred = [t for t in ("Operator", "Individual") if t in type_values]
    rest = [t for t in type_values if t not in preferred]
    type_options = ["All types"] + preferred + rest
    if "day_type" in st.session_state and st.session_state["day_type"] not in type_options:
        st.session_state["day_type"] = "All types"
    if "day_type" not in st.session_state:
        st.session_state["day_type"] = "All types"

    f1, f2, f3, f4, f5 = st.columns([1, 1, 1.1, 1.5, 1])
    with f1:
        start_date = st.selectbox("Start", options=available_dates, key="day_start")
    with f2:
        end_date = st.selectbox("End", options=available_dates, key="day_end")
    with f3:
        city = st.selectbox("City", options=city_options, key="day_city")
    with f4:
        partner = st.selectbox("Partner", options=partner_options, key="day_partner")
    with f5:
        type_pick = st.selectbox("Type", options=type_options, key="day_type")

    st.markdown('<div class="section-rule"></div>', unsafe_allow_html=True)

    try:
        trend = load_run_on_trend(fp, city)
    except Exception:
        trend = pd.DataFrame(columns=["Date", "Run On", "Vehicles"])
    try:
        ageing_trend = load_ageing_trend(fp, city)
    except Exception:
        ageing_trend = pd.DataFrame(columns=["Date", "Ageing", "Vehicles"])
    try:
        rev_trend = load_revenue_deadmile_trend(fp, city)
    except Exception:
        rev_trend = pd.DataFrame(columns=["Date", "Metric", "Amount"])

    left, right = st.columns(2)
    with left:
        _render_run_on_chart(trend)
    with right:
        _render_ageing_chart(ageing_trend)

    st.markdown('<div class="section-rule"></div>', unsafe_allow_html=True)
    _render_revenue_deadmile_chart(rev_trend)

    st.markdown('<div class="section-rule"></div>', unsafe_allow_html=True)
    _render_filtered_views(table, partner, type_pick, city)


if __name__ == "__main__":
    main()
