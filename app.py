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
from lib.rapido_process import (
    build_rapido_vehicle_days,
    list_rapido_files,
    rapido_dir,
)
from lib.drive_sync import drive_configured, ensure_data_ready
from lib.paths import data_root
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
    parts = ["v26-run-on-mix", source_fingerprint(uber_root())]
    for path in list_allocation_files(allocation_dir()):
        parts.append(f"alloc:{_file_sig(path)}")
    for path in list_ola_files():
        parts.append(f"ola:{_file_sig(path)}")
    for path in list_gps_files():
        parts.append(f"gps:{_file_sig(path)}")
    for path in list_rapido_files():
        parts.append(f"rapido:{_file_sig(path)}")
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


@st.cache_data(show_spinner="Loading Rapido metrics…", ttl=3600)
def cached_rapido_days(fingerprint: str):
    _ = fingerprint
    return build_rapido_vehicle_days(rapido_dir())


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
    try:
        rapido_days, _ = cached_rapido_days(fingerprint)
        if not rapido_days.empty:
            dates.update(
                rapido_days["Date"].dt.strftime("%Y-%m-%d").dropna().unique().tolist()
            )
    except Exception:
        pass
    return sorted(dates)


@st.cache_data(show_spinner="Building Run On trend…")
def load_run_on_trend(fingerprint: str, city: str = "All cities") -> pd.DataFrame:
    uber_days, _ = cached_uber_days(fingerprint)
    ola_days, _ = cached_ola_days(fingerprint)
    rapido_days, _ = cached_rapido_days(fingerprint)
    alloc, _ = cached_allocation(fingerprint)
    return run_on_daily_counts(
        uber_days,
        ola_days,
        last_n_days=7,
        alloc=alloc,
        city=None if city == "All cities" else city,
        rapido_days=rapido_days,
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
    rapido_days, _ = cached_rapido_days(fingerprint)
    return assemble_fleet_table(
        start_date,
        end_date,
        alloc,
        alloc_meta,
        uber_days,
        ola_days,
        gps_days,
        rapido_days,
        write_output=False,
    )


def _chart_day_label(value) -> str:
    """Chart x-axis: 11-7 with weekday letter below (M T W T F S S)."""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return str(value)
    # Mon=0 … Sun=6
    letters = ("M", "T", "W", "T", "F", "S", "S")
    return f"{int(ts.day)}-{int(ts.month)}<br>{letters[int(ts.dayofweek)]}"


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
    plot_df["Date"] = plot_df["Date"].map(_chart_day_label)
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
        labels={"Vehicles": "Vehicles", "Date": "", color_col: color_col},
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
        xaxis=dict(gridcolor="#DCE6E1", tickangle=0, type="category", title=""),
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
    """One bar per day · OLA / Uber / Rapido / Mix stacked · visible labels."""
    if trend.empty:
        return
    st.subheader("Run On")
    st.caption("Last 7 days · one bar per day · OLA / Uber / Rapido / Mix stacked")

    plot_df = trend.copy()
    plot_df["Date"] = plot_df["Date"].map(_chart_day_label)
    plot_df["_label"] = plot_df["Vehicles"].map(
        lambda v: "" if int(v) == 0 else str(int(v))
    )

    fig = px.bar(
        plot_df,
        x="Date",
        y="Vehicles",
        color="Run On",
        barmode="stack",
        text="_label",
        category_orders={"Run On": ["OLA", "Uber", "Rapido", "Mix"]},
        color_discrete_map={
            "OLA": "#0F6E56",
            "Uber": "#1B4F9C",
            "Rapido": "#7A3E9D",
            "Mix": "#C45C26",
        },
        labels={"Vehicles": "Vehicles", "Date": "", "Run On": "Run On"},
    )
    fig.update_traces(
        textposition="inside",
        insidetextanchor="middle",
        cliponaxis=False,
        textfont=dict(size=12, family="DM Sans", color="#FFFFFF"),
        marker_line_width=0,
        textangle=0,
    )
    # Day totals on top of each stacked bar
    day_tot = (
        plot_df.groupby("Date", as_index=False)["Vehicles"]
        .sum()
        .rename(columns={"Vehicles": "Total"})
    )
    y_max = float(day_tot["Total"].max()) if len(day_tot) else 0
    fig.add_scatter(
        x=day_tot["Date"],
        y=day_tot["Total"],
        mode="text",
        text=day_tot["Total"].map(lambda v: str(int(v)) if int(v) else ""),
        textposition="top center",
        textfont=dict(size=13, family="DM Sans", color="#0B1F18"),
        showlegend=False,
        hoverinfo="skip",
    )
    fig.update_layout(
        height=360,
        margin=dict(l=10, r=20, t=55, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="DM Sans", color="#1A2B24", size=11),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, x=0, font=dict(size=11)),
        xaxis=dict(gridcolor="#DCE6E1", tickangle=0, type="category", title=""),
        yaxis=dict(
            gridcolor="#DCE6E1",
            title="Vehicles",
            range=[0, y_max * 1.25 + 5],
        ),
        bargap=0.35,
    )
    st.plotly_chart(fig, use_container_width=True)


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
    plot_df["Date"] = plot_df["Date"].map(_chart_day_label)
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
        labels={"Amount": "Amount", "Date": "", "Metric": "Metric"},
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
        xaxis=dict(gridcolor="#DCE6E1", tickangle=0, type="category", title=""),
        yaxis=dict(
            gridcolor="#DCE6E1",
            title="Amount",
            range=[0, y_max * 1.25 + 5],
        ),
        uniformtext_minsize=9,
        uniformtext_mode="show",
    )
    st.plotly_chart(fig, use_container_width=True)


def _month_label(start_date: str, end_date: str) -> str:
    try:
        s = pd.Timestamp(start_date)
        e = pd.Timestamp(end_date)
    except Exception:
        return f"{start_date} → {end_date}"
    if s.strftime("%Y-%m") == e.strftime("%Y-%m"):
        return s.strftime("%B %Y")
    return f"{s.strftime('%d %b %Y')} → {e.strftime('%d %b %Y')}"


def _current_month_range(available_dates: list[str]) -> tuple[str, str] | None:
    """First/last available dates that fall in the current calendar month."""
    if not available_dates:
        return None
    today = pd.Timestamp.today().normalize()
    month_key = today.strftime("%Y-%m")
    in_month = [d for d in available_dates if str(d).startswith(month_key)]
    if not in_month:
        return None
    return in_month[0], in_month[-1]


def _render_km_mix_pie(df: pd.DataFrame, *, period_label: str) -> None:
    """Pie on left · full labels on right (Unproductive always listed)."""
    st.subheader("KM mix")
    st.caption(f"Current month (fixed) · {period_label}")

    if df is None or df.empty:
        st.info("No vehicles in this filter for the pie chart.")
        return

    needed = ("Total Intrip KM", "Approved KM", "Dead KM", "Dead KM Type")
    if any(c not in df.columns for c in needed):
        st.info("KM columns not ready yet — refresh data.")
        return

    dead = df["Dead KM"].fillna(0).clip(lower=0)
    dtype = df["Dead KM Type"].fillna("").astype(str).str.strip()
    deadmile_km = float(dead[dtype == "Deadmile"].sum())
    unproductive_km = float(dead[dtype == "Unproductive"].sum())

    colors = {
        "Intrip KM": "#0F6E56",
        "Approved KM": "#1B4F9C",
        "Deadmile": "#C45C26",
        "Unproductive": "#8B2942",
    }
    # Always keep all 4 rows so Unproductive never disappears from labels
    pie_df = pd.DataFrame(
        {
            "Category": ["Intrip KM", "Approved KM", "Deadmile", "Unproductive"],
            "KM": [
                float(df["Total Intrip KM"].fillna(0).clip(lower=0).sum()),
                float(df["Approved KM"].fillna(0).clip(lower=0).sum()),
                deadmile_km,
                unproductive_km,
            ],
        }
    )
    total = float(pie_df["KM"].clip(lower=0).sum())
    if total <= 0:
        st.info("No KM totals to chart for this period.")
        return

    pie_df["Pct"] = pie_df["KM"].clip(lower=0) / total
    pie_df["Color"] = pie_df["Category"].map(colors)

    # Chart data: still plot zeros as tiny so order stays stable — skip exact 0
    chart_df = pie_df[pie_df["KM"] > 0].copy()
    if chart_df.empty:
        st.info("No KM totals to chart for this period.")
        return

    pie_col, label_col = st.columns([2.4, 0.85])
    with pie_col:
        pulls = [
            0.18 if c == "Unproductive" else 0.02 for c in chart_df["Category"]
        ]
        text_pos = [
            "outside" if c == "Unproductive" or float(p) < 0.08 else "inside"
            for c, p in zip(chart_df["Category"], chart_df["Pct"])
        ]
        text_colors = [
            "#1A2B24" if pos == "outside" else "#FFFFFF" for pos in text_pos
        ]
        fig = px.pie(
            chart_df,
            names="Category",
            values="KM",
            color="Category",
            color_discrete_map=colors,
            category_orders={
                "Category": ["Intrip KM", "Approved KM", "Deadmile", "Unproductive"],
            },
            hole=0.4,
        )
        fig.update_traces(
            textposition=text_pos,
            textinfo="label+percent",
            texttemplate="%{label}<br>%{percent:.1%}",
            hovertemplate="<b>%{label}</b><br>%{value:,.0f} km<br>%{percent:.1%}<extra></extra>",
            marker=dict(line=dict(color="#F7FAF8", width=3)),
            insidetextorientation="horizontal",
            pull=pulls,
            showlegend=False,
            sort=False,
        )
        if fig.data:
            fig.data[0].textfont = dict(
                size=12, family="DM Sans", color=text_colors
            )
        fig.update_layout(
            height=380,
            margin=dict(l=8, r=8, t=8, b=8),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
            annotations=[
                dict(
                    text=f"<b>{total:,.0f}</b><br>total km",
                    x=0.5,
                    y=0.5,
                    showarrow=False,
                    font=dict(size=14, family="DM Sans", color="#1A2B24"),
                    align="center",
                )
            ],
        )
        st.plotly_chart(fig, use_container_width=True)

    with label_col:
        rows_html = []
        for _, row in pie_df.iterrows():
            highlight = row["Category"] == "Unproductive"
            bg = "background:#F8ECEF;" if highlight else "background:#F4F8F6;"
            rows_html.append(
                f"""
                <div style="
                    {bg}
                    border-radius:5px; padding:0.2rem 0.35rem; margin:0 0 0.2rem;
                    font-family:'DM Sans',sans-serif;
                    display:flex; align-items:center; gap:0.3rem;
                ">
                  <span style="
                      width:7px; height:7px; border-radius:2px; flex-shrink:0;
                      background:{row['Color']};
                  "></span>
                  <span style="font-weight:600; color:#12241C; font-size:0.68rem;
                               white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                    {row['Category']}
                  </span>
                  <span style="margin-left:auto; color:#2A3F36; font-size:0.68rem;
                               white-space:nowrap;">
                    {row['KM']:,.0f}
                  </span>
                </div>
                """
            )
        st.markdown("".join(rows_html), unsafe_allow_html=True)


def _render_filtered_views(
    table: pd.DataFrame,
    partner: str,
    type_pick: str,
    city: str,
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
    money_cols = (
        "Total Revenue",
        "Total Cash Collection",
        "Revenue",
        "Cash Collection",
        "Ola Customer Bill",
        "Ola Cash Collected",
    )
    for col in money_cols:
        type_summary[col] = (type_summary[col] / 1e5).round(2)
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
    ageing_summary["Total Revenue"] = (ageing_summary["Total Revenue"] / 1e5).round(2)
    ageing_summary["Total Cash Collection"] = (
        ageing_summary["Total Cash Collection"] / 1e5
    ).round(2)
    ageing_summary["Total Trip"] = ageing_summary["Total Trip"].astype(int)
    ageing_summary["Vehicle Count"] = ageing_summary["Vehicle Count"].astype(int)

    money_cfg = {
        "Type": st.column_config.TextColumn("Type", alignment="center"),
        "Total Revenue": st.column_config.NumberColumn(
            "Total Revenue (L)", format="₹%.2f", alignment="center"
        ),
        "Total Cash Collection": st.column_config.NumberColumn(
            "Total Cash (L)", format="₹%.2f", alignment="center"
        ),
        "Revenue": st.column_config.NumberColumn(
            "Uber Rev (L)", format="₹%.2f", alignment="center"
        ),
        "Cash Collection": st.column_config.NumberColumn(
            "Uber Cash (L)", format="₹%.2f", alignment="center"
        ),
        "Ola Customer Bill": st.column_config.NumberColumn(
            "Ola Bill (L)", format="₹%.2f", alignment="center"
        ),
        "Ola Cash Collected": st.column_config.NumberColumn(
            "Ola Cash (L)", format="₹%.2f", alignment="center"
        ),
        "Total Trip": st.column_config.NumberColumn(
            "Total Trip", format="localized", alignment="center"
        ),
        "Vehicle Count": st.column_config.NumberColumn(
            "Vehicle Count", format="localized", alignment="center"
        ),
    }

    ageing_cfg = {
        "Ageing": st.column_config.TextColumn("Ageing", alignment="center"),
        "Total Revenue": st.column_config.NumberColumn(
            "Total Revenue (L)", format="₹%.2f", alignment="center"
        ),
        "Total Cash Collection": st.column_config.NumberColumn(
            "Total Cash (L)", format="₹%.2f", alignment="center"
        ),
        "Total Trip": st.column_config.NumberColumn(
            "Total Trip", format="localized", alignment="center"
        ),
        "Vehicle Count": st.column_config.NumberColumn(
            "Vehicle Count", format="localized", alignment="center"
        ),
    }

    s1, s2 = st.columns(2)
    with s1:
        st.subheader("Type summary")
        st.caption("Money values in ₹ Lakh")
        st.dataframe(
            type_summary,
            use_container_width=True,
            hide_index=True,
            height=200,
            column_config=money_cfg,
        )
    with s2:
        st.subheader("Ageing summary")
        st.caption("Money values in ₹ Lakh")
        st.dataframe(
            ageing_summary,
            use_container_width=True,
            hide_index=True,
            height=200,
            column_config=ageing_cfg,
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
        "Totals first · Total Revenue = Uber + Ola + Rapido · "
        "Total Cash = Uber + Ola cash · Total Trip = Uber + Ola + Rapido · "
        "Total Intrip KM = Uber Trip Distance + Ola Actual Kms · "
        "Ideal KM = (30 × days) + (3 × trips) + Intrip · "
        "Approved KM = min(GPS, Ideal) − Intrip · "
        "Buffer KM = GPS − Ideal · Deadmile Charges = max(0, Buffer) × 3 · "
        "Dead KM = max(0, GPS − Ideal) · Type = Unproductive / Deadmile · "
        "Rapido Revenue = captain_earnings + toll · Ride Time = trip ride_time sum"
    )
    # Display copy with comma-separated numbers (keeps underlying sort on view)
    display = view.copy()
    money_km_cols = [
        "Total Revenue",
        "Total Cash Collection",
        "Total Intrip KM",
        "GPS KMs",
        "Ideal KM",
        "Approved KM",
        "Buffer KM",
        "Deadmile Charges",
        "Dead KM",
        "Revenue",
        "Cash Collection",
        "Ola Customer Bill",
        "Ola Cash Collected",
        "Rapido Revenue",
        "Trip Distance",
        "Ola Actual Kms",
        "Rapido Ride Time",
    ]
    int_cols = [
        "Total Trip",
        "Trip Completed Count",
        "Ola Trips",
        "Rapido Trips",
        "Ola Trip Time",
    ]
    for col in money_km_cols:
        if col in display.columns:
            display[col] = display[col].map(
                lambda v: f"{float(v):,.2f}" if pd.notna(v) else ""
            )
    for col in int_cols:
        if col in display.columns:
            display[col] = display[col].map(
                lambda v: f"{int(float(v)):,}" if pd.notna(v) else ""
            )

    st.dataframe(
        display,
        use_container_width=True,
        height=560,
        hide_index=True,
        column_config={
            "Total Revenue": st.column_config.TextColumn("Total Revenue"),
            "Total Cash Collection": st.column_config.TextColumn(
                "Total Cash Collection"
            ),
            "Total Trip": st.column_config.TextColumn("Total Trip"),
            "Total Intrip KM": st.column_config.TextColumn("Total Intrip KM"),
            "GPS KMs": st.column_config.TextColumn("GPS KMs"),
            "Ideal KM": st.column_config.TextColumn("Ideal KM"),
            "Approved KM": st.column_config.TextColumn("Approved KM"),
            "Buffer KM": st.column_config.TextColumn("Buffer KM"),
            "Deadmile Charges": st.column_config.TextColumn("Deadmile Charges"),
            "Dead KM": st.column_config.TextColumn("Dead KM"),
            "Dead KM Type": st.column_config.TextColumn("Dead KM Type"),
            "Run On": st.column_config.TextColumn("Run On"),
            "Ageing": st.column_config.TextColumn("Ageing"),
            "Revenue": st.column_config.TextColumn("Uber Revenue"),
            "Cash Collection": st.column_config.TextColumn("Uber Cash"),
            "Ola Customer Bill": st.column_config.TextColumn("Ola Customer Bill"),
            "Ola Cash Collected": st.column_config.TextColumn("Ola Cash Collected"),
            "Rapido Revenue": st.column_config.TextColumn("Rapido Revenue"),
            "Trip Distance": st.column_config.TextColumn("Uber Trip Distance"),
            "Ola Actual Kms": st.column_config.TextColumn("Ola Actual Kms"),
            "Ola Trip Time": st.column_config.TextColumn("Ola Trip Time"),
            "Rapido Ride Time": st.column_config.TextColumn("Rapido Ride Time"),
            "Trip Completed Count": st.column_config.TextColumn("Uber Trips"),
            "Ola Trips": st.column_config.TextColumn("Ola Trips"),
            "Rapido Trips": st.column_config.TextColumn("Rapido Trips"),
        },
    )


def _render_settings() -> None:
    st.title("Settings")
    st.caption("Data paths · Drive sync · cache")

    st.subheader("Data")
    st.write(
        f"**Data root:** `{data_root()}`  \n"
        f"**Uber:** `{uber_root()}`  \n"
        f"**OLA:** `{ola_dir()}`  \n"
        f"**GPS:** `{gps_dir()}`  \n"
        f"**Rapido:** `{rapido_dir()}`  \n"
        f"**Allocation:** `{allocation_dir()}`"
    )

    st.markdown('<div class="section-rule"></div>', unsafe_allow_html=True)
    st.subheader("Google Drive (Streamlit Cloud)")
    if drive_configured():
        st.success("Drive secrets found — Cloud can sync heavy data from Drive.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Sync Drive now", type="primary"):
                info = ensure_data_ready(force=True, show_status=True)
                st.session_state["drive_sync_info"] = info
                st.cache_data.clear()
                st.rerun()
        with c2:
            if st.button("Clear cache & reload"):
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
        info = st.session_state.get("drive_sync_info")
        if info:
            st.caption(
                f"Last sync · mode={info.get('mode')} · "
                f"downloaded={info.get('downloaded')} · cached={info.get('skipped')}"
            )
    else:
        st.info(
            "Local mode (no Drive secrets). On Streamlit Cloud add Secrets so "
            "Uber/OLA/GPS/Rapido stay on Drive and sync here."
        )
        st.markdown(
            """
**Cloud setup (one time)**  
1. Google Cloud → enable **Drive API** → create **service account** → JSON key  
2. Share Drive folder **AI Dashboard** (and optionally **Rapido Data**) with the service account email as **Viewer**  
3. Streamlit Cloud → **Settings → Secrets** — paste service account JSON under `[gcp_service_account]` and folder ID:

```toml
[drive]
root_folder_id = "YOUR_AI_DASHBOARD_FOLDER_ID"
rapido_data_folder_id = ""   # optional
```

Folder ID = last part of Drive folder URL.  
Heavy files download once, then only when changed.
            """
        )
        if st.button("Clear cache & reload", type="primary"):
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
            st.success("Cache cleared.")
            st.rerun()

    st.markdown('<div class="section-rule"></div>', unsafe_allow_html=True)
    st.subheader("Notes")
    st.markdown(
        """
- Heavy data stays on **Google Drive** (not GitHub)
- Local PC: uses `G:\\My Drive\\...` folders as before
- Cloud: syncs into `.data_cache` then builds the same tables
        """
    )


def _render_dashboard(fp: str, available_dates: list[str]) -> None:
    st.title("Fleet day table")
    st.caption(
        "Uber date = Drop-off 4am→4am (file 15–16 → day 15) · "
        "Ola / Rapido = calendar date · First load slow once, then cached"
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

    month_bounds = _current_month_range(available_dates)
    pie_table: pd.DataFrame | None = None
    pie_start = pie_end = ""
    if month_bounds:
        pie_start, pie_end = month_bounds
        try:
            if pie_start == start_date and pie_end == end_date:
                pie_table = table
            else:
                pie_table, _ = load_range_table(pie_start, pie_end, fp)
        except Exception:
            pie_table = None

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

    # Filter current-month pie by City / Partner / Type (dates stay frozen)
    pie_base = pie_table if pie_table is not None else pd.DataFrame()
    if not pie_base.empty:
        if city != "All cities":
            pie_base = pie_base[
                pie_base["City"].fillna("").astype(str).str.strip() == city
            ]
        if partner != "All partners":
            pie_base = pie_base[
                pie_base["Partner Name"].fillna("").astype(str).str.strip() == partner
            ]
        if type_pick != "All types":
            pie_base = pie_base[
                pie_base["Type"].fillna("").astype(str).str.strip() == type_pick
            ]

    row2_l, row2_r = st.columns(2)
    with row2_l:
        _render_revenue_deadmile_chart(rev_trend)
    with row2_r:
        if pie_start and pie_end:
            _render_km_mix_pie(
                pie_base,
                period_label=_month_label(pie_start, pie_end),
            )
        else:
            st.subheader("KM mix")
            st.caption("Current month (fixed) · no data for this month yet")
            st.info("No available dates in the current calendar month.")

    st.markdown('<div class="section-rule"></div>', unsafe_allow_html=True)
    _render_filtered_views(table, partner, type_pick, city)


def main():
    # Drive sync once per session (Cloud). Local without secrets = no-op.
    if "drive_ready" not in st.session_state:
        st.session_state["drive_sync_info"] = ensure_data_ready(
            force=False, show_status=drive_configured()
        )
        st.session_state["drive_ready"] = True

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

    with st.sidebar:
        st.markdown('<div class="brand">AI Dashboard</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="brand-sub">Allocation + Uber + Ola + Rapido</div>',
            unsafe_allow_html=True,
        )
        page = st.radio(
            "Navigate",
            options=["Dashboard", "Settings"],
            key="nav_page",
            label_visibility="collapsed",
        )
        st.markdown('<div class="section-rule"></div>', unsafe_allow_html=True)
        if page == "Dashboard" and st.button(
            "Refresh data now", use_container_width=True
        ):
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

    if page == "Settings":
        _render_settings()
        return

    fp = current_data_fingerprint()
    try:
        available_dates = load_available_dates(fp)
    except Exception:
        available_dates = []

    _render_dashboard(fp, available_dates)


if __name__ == "__main__":
    main()
