"""Allocation vehicles for date range, then Uber + Ola metrics attached."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from lib.allocation import _norm_partner_id, _norm_vehicle, load_allocation
from lib.ola_process import build_ola_vehicle_days, ola_dir
from lib.gps_process import build_gps_vehicle_days, gps_dir
from lib.rapido_process import build_rapido_vehicle_days, rapido_dir
from lib.uber_process import build_uber_vehicle_days

TABLE_COLS = [
    "Date",
    "Vehicle Number",
    "City",
    "Partner ID",
    "Partner Name",
    "Type",
    "Type Of Plan",
    "DM Name",
    "Latest Allocation Date",
    "Drop Off Date",
    "Run On",
    "Ageing",
    "Total Revenue",
    "Total Cash Collection",
    "Total Trip",
    "Total Intrip KM",
    "GPS KMs",
    "Ideal KM",
    "Approved KM",
    "Buffer KM",
    "Deadmile Charges",
    "Dead KM",
    "Dead KM Type",
    "Revenue",
    "Cash Collection",
    "Ola Customer Bill",
    "Ola Cash Collected",
    "Rapido Revenue",
    "Rapido Ride Time",
    "Trip Completed Count",
    "Ola Trips",
    "Rapido Trips",
    "Trip Distance",
    "Ola Actual Kms",
    "Ola Trip Time",
]

# Status-like values that appear in partner IDs column — not real partners
_FAKE_PARTNER_IDS = {
    "",
    "-",
    "RFD",
    "MAINTENANCE",
    "DROP OFF",
    "ALLOCATION",
    "ACTIVE",
    "SAME DAY D&A",
    "NEW DEPLOYMENT",
    "NAN",
    "NONE",
    "NULL",
}
_ONROAD_STATUSES = {"Active", "Allocation", "Same Day D&A"}


def _is_real_partner_id(value) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return text.upper() not in _FAKE_PARTNER_IDS


def _assign_ageing(partner_ids: pd.Series, total_revenue: pd.Series) -> pd.Series:
    """Ageing from Total Revenue + real Partner ID (unique-vehicle friendly)."""
    has_id = partner_ids.map(_is_real_partner_id)
    rev = pd.to_numeric(total_revenue, errors="coerce").fillna(0)
    out = pd.Series("", index=partner_ids.index, dtype=object)
    out.loc[has_id & (rev <= 0)] = "Not running"
    out.loc[rev > 2500] = "> 2500"
    out.loc[(rev > 0) & (rev <= 2500)] = "< 2500"
    return out


def build_uber_day_with_allocation(
    on_date: str = "2026-07-15",
    project_root: Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    return build_uber_range_with_allocation(on_date, on_date, project_root)


def _empty_table() -> pd.DataFrame:
    return pd.DataFrame(columns=TABLE_COLS)


def run_on_daily_counts(
    uber_days: pd.DataFrame,
    ola_days: pd.DataFrame,
    *,
    last_n_days: int = 7,
    end_date: str | None = None,
    alloc: pd.DataFrame | None = None,
    city: str | None = None,
    rapido_days: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Unique vehicle counts per day by Run On: OLA / Uber / Rapido / Mix.
    Mix = active on 2+ platforms the same day.
    Fixed window of last_n_days ending at end_date (or max activity date).
    Optional city filter uses allocation City on that day.
    """
    empty = pd.DataFrame(columns=["Date", "Run On", "Vehicles"])
    categories = ["OLA", "Uber", "Rapido", "Mix"]

    uber = uber_days.copy() if uber_days is not None and not uber_days.empty else pd.DataFrame()
    ola = ola_days.copy() if ola_days is not None and not ola_days.empty else pd.DataFrame()
    rapido = (
        rapido_days.copy()
        if rapido_days is not None and not rapido_days.empty
        else pd.DataFrame()
    )

    if not uber.empty:
        uber_active = uber[
            (uber["Trip Completed Count"] > 0) | (uber["Revenue"] != 0)
        ][["Vehicle Number", "Date"]].drop_duplicates()
        uber_active["_uber"] = True
    else:
        uber_active = pd.DataFrame(columns=["Vehicle Number", "Date", "_uber"])

    if not ola.empty:
        ola_active = ola[
            (ola["Ola Trips"] > 0) | (ola["Ola Customer Bill"] != 0)
        ][["Vehicle Number", "Date"]].drop_duplicates()
        ola_active["_ola"] = True
    else:
        ola_active = pd.DataFrame(columns=["Vehicle Number", "Date", "_ola"])

    if not rapido.empty:
        rapido_active = rapido[
            (rapido["Rapido Trips"] > 0) | (rapido["Rapido Revenue"] != 0)
        ][["Vehicle Number", "Date"]].drop_duplicates()
        rapido_active["_rapido"] = True
    else:
        rapido_active = pd.DataFrame(columns=["Vehicle Number", "Date", "_rapido"])

    if uber_active.empty and ola_active.empty and rapido_active.empty:
        return empty

    merged = uber_active.merge(ola_active, on=["Vehicle Number", "Date"], how="outer")
    merged = merged.merge(rapido_active, on=["Vehicle Number", "Date"], how="outer")
    merged["_uber"] = merged["_uber"].fillna(False).astype(bool)
    merged["_ola"] = merged["_ola"].fillna(False).astype(bool)
    merged["_rapido"] = merged["_rapido"].fillna(False).astype(bool)
    merged["Date"] = pd.to_datetime(merged["Date"], errors="coerce").dt.normalize()
    merged = merged[merged["Date"].notna()].copy()

    if end_date:
        end = pd.Timestamp(end_date).normalize()
    else:
        end = merged["Date"].max()
    start = end - pd.Timedelta(days=last_n_days - 1)
    merged = merged[(merged["Date"] >= start) & (merged["Date"] <= end)].copy()
    if merged.empty:
        return empty

    if (
        city
        and city not in {"All cities", "All Cities", ""}
        and alloc is not None
        and not alloc.empty
        and "City" in alloc.columns
    ):
        city_days = alloc[
            (alloc["Date"] >= start)
            & (alloc["Date"] <= end)
            & (alloc["City"].fillna("").astype(str).str.strip() == city)
        ][["Vehicle Number", "Date"]].drop_duplicates()
        city_days["Date"] = pd.to_datetime(city_days["Date"], errors="coerce").dt.normalize()
        merged = merged.merge(city_days, on=["Vehicle Number", "Date"], how="inner")
        if merged.empty:
            all_days = pd.date_range(start, end, freq="D")
            grid = pd.MultiIndex.from_product(
                [all_days, categories], names=["Date", "Run On"]
            ).to_frame(index=False)
            grid["Vehicles"] = 0
            grid["Date"] = grid["Date"].dt.strftime("%Y-%m-%d")
            return grid.reset_index(drop=True)

    def _label(row) -> str:
        n = int(row["_ola"]) + int(row["_uber"]) + int(row["_rapido"])
        if n >= 2:
            return "Mix"
        if row["_ola"]:
            return "OLA"
        if row["_uber"]:
            return "Uber"
        if row["_rapido"]:
            return "Rapido"
        return ""

    merged["Run On"] = merged.apply(_label, axis=1)
    merged = merged[merged["Run On"] != ""].copy()

    daily = (
        merged.groupby(["Date", "Run On"], as_index=False)
        .agg(Vehicles=("Vehicle Number", "nunique"))
        .sort_values(["Date", "Run On"])
    )

    all_days = pd.date_range(start, end, freq="D")
    grid = pd.MultiIndex.from_product(
        [all_days, categories], names=["Date", "Run On"]
    ).to_frame(index=False)
    daily = grid.merge(daily, on=["Date", "Run On"], how="left")
    daily["Vehicles"] = daily["Vehicles"].fillna(0).astype(int)
    daily["Date"] = daily["Date"].dt.strftime("%Y-%m-%d")
    return daily.reset_index(drop=True)


def ageing_daily_counts(
    alloc: pd.DataFrame,
    uber_days: pd.DataFrame,
    ola_days: pd.DataFrame,
    *,
    last_n_days: int = 7,
    end_date: str | None = None,
    city: str | None = None,
) -> pd.DataFrame:
    """
    Unique on-road vehicles per day by Ageing (< 2500 / > 2500 / Not running).

    - One row per Vehicle Number per day (nunique)
    - Only Final Status Active / Allocation / Same Day D&A
    - Real Partner ID only for Not running (excludes RFD / Maintenance as IDs)
    - Window ends at last Uber/Ola activity day (not empty allocation-only days)
    """
    empty = pd.DataFrame(columns=["Date", "Ageing", "Vehicles"])
    categories = ["< 2500", "> 2500", "Not running"]

    if alloc is None or alloc.empty:
        return empty

    activity_ends: list[pd.Timestamp] = []
    for frame in (uber_days, ola_days):
        if frame is not None and not frame.empty and "Date" in frame.columns:
            mx = pd.to_datetime(frame["Date"], errors="coerce").max()
            if pd.notna(mx):
                activity_ends.append(pd.Timestamp(mx).normalize())
    if end_date:
        end = pd.Timestamp(end_date).normalize()
    elif activity_ends:
        end = max(activity_ends)
    else:
        end = pd.Timestamp(alloc["Date"].max()).normalize()

    start = end - pd.Timedelta(days=last_n_days - 1)
    all_days = pd.date_range(start, end, freq="D")

    uber = uber_days if uber_days is not None else pd.DataFrame()
    ola = ola_days if ola_days is not None else pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for day in all_days:
        day_alloc = alloc[alloc["Date"] == day].copy()
        if day_alloc.empty:
            continue
        if city and city not in {"All cities", "All Cities", ""}:
            day_alloc = day_alloc[
                day_alloc["City"].fillna("").astype(str).str.strip() == city
            ]
        if day_alloc.empty:
            continue

        # Unique vehicle per day (last row if duplicates)
        day_alloc = day_alloc.sort_values(["Vehicle Number", "Date"])
        day_alloc = day_alloc.drop_duplicates("Vehicle Number", keep="last")

        status = day_alloc.get("Final Status", pd.Series("", index=day_alloc.index))
        status = status.fillna("").astype(str).str.strip()
        day_alloc = day_alloc[status.isin(_ONROAD_STATUSES)].copy()
        if day_alloc.empty:
            continue

        veh = day_alloc[["Vehicle Number"]].copy()
        if "partner IDs" in day_alloc.columns:
            veh["Partner ID"] = day_alloc["partner IDs"].values
        else:
            veh["Partner ID"] = ""

        # Day metrics — unique vehicle keys
        u = _agg_metric_days(
            uber,
            day,
            day,
            ["Revenue", "Cash Collection", "Trip Completed Count", "Trip Distance"],
        )
        o = _agg_metric_days(
            ola,
            day,
            day,
            [
                "Ola Customer Bill",
                "Ola Cash Collected",
                "Ola Actual Kms",
                "Ola Trip Time",
                "Ola Trips",
            ],
        )
        veh = veh.merge(u, on="Vehicle Number", how="left")
        veh = veh.merge(o, on="Vehicle Number", how="left")
        for col in ("Revenue", "Ola Customer Bill"):
            if col not in veh.columns:
                veh[col] = 0
            veh[col] = veh[col].fillna(0)
        veh["Total Revenue"] = (veh["Revenue"] + veh["Ola Customer Bill"]).round(2)
        veh["Ageing"] = _assign_ageing(veh["Partner ID"], veh["Total Revenue"])
        veh = veh[veh["Ageing"].isin(categories)].drop_duplicates("Vehicle Number")

        part = (
            veh.groupby("Ageing", as_index=False)
            .agg(Vehicles=("Vehicle Number", "nunique"))
        )
        part["Date"] = day
        frames.append(part)

    if frames:
        daily = pd.concat(frames, ignore_index=True)
    else:
        daily = pd.DataFrame(columns=["Date", "Ageing", "Vehicles"])

    grid = pd.MultiIndex.from_product(
        [all_days, categories], names=["Date", "Ageing"]
    ).to_frame(index=False)
    daily = grid.merge(daily, on=["Date", "Ageing"], how="left")
    daily["Vehicles"] = daily["Vehicles"].fillna(0).astype(int)
    daily["Date"] = daily["Date"].dt.strftime("%Y-%m-%d")
    return daily.reset_index(drop=True)


def revenue_deadmile_daily(
    alloc: pd.DataFrame,
    uber_days: pd.DataFrame,
    ola_days: pd.DataFrame,
    gps_days: pd.DataFrame | None = None,
    *,
    last_n_days: int = 7,
    end_date: str | None = None,
    city: str | None = None,
) -> pd.DataFrame:
    """
    Daily totals for last_n_days:
      - Total Revenue
      - Deadmile Charges where Dead KM Type = Unproductive
      - Deadmile Charges where Dead KM Type = Deadmile
    Long format: Date, Metric, Amount.
    """
    categories = ["Total Revenue", "Unproductive", "Deadmile"]
    empty = pd.DataFrame(columns=["Date", "Metric", "Amount"])

    if alloc is None or alloc.empty:
        return empty

    activity_ends: list[pd.Timestamp] = []
    for frame in (uber_days, ola_days, gps_days):
        if frame is not None and not frame.empty and "Date" in frame.columns:
            mx = pd.to_datetime(frame["Date"], errors="coerce").max()
            if pd.notna(mx):
                activity_ends.append(pd.Timestamp(mx).normalize())
    if end_date:
        end = pd.Timestamp(end_date).normalize()
    elif activity_ends:
        end = max(activity_ends)
    else:
        end = pd.Timestamp(alloc["Date"].max()).normalize()

    start = end - pd.Timedelta(days=last_n_days - 1)
    all_days = pd.date_range(start, end, freq="D")

    uber = uber_days if uber_days is not None else pd.DataFrame()
    ola = ola_days if ola_days is not None else pd.DataFrame()
    gps = gps_days if gps_days is not None else pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for day in all_days:
        day_alloc = alloc[alloc["Date"] == day].copy()
        if day_alloc.empty:
            continue
        if city and city not in {"All cities", "All Cities", ""}:
            day_alloc = day_alloc[
                day_alloc["City"].fillna("").astype(str).str.strip() == city
            ]
        if day_alloc.empty:
            continue

        day_alloc = day_alloc.sort_values(["Vehicle Number", "Date"])
        day_alloc = day_alloc.drop_duplicates("Vehicle Number", keep="last")

        veh = day_alloc[["Vehicle Number"]].copy()
        if "partner IDs" in day_alloc.columns:
            veh["Partner ID"] = day_alloc["partner IDs"].map(_norm_partner_id)
        else:
            veh["Partner ID"] = ""

        u = _agg_metric_days(
            uber,
            day,
            day,
            ["Revenue", "Trip Completed Count", "Trip Distance"],
        )
        o = _agg_metric_days(
            ola,
            day,
            day,
            ["Ola Customer Bill", "Ola Actual Kms", "Ola Trips"],
        )
        g = _agg_metric_days(gps, day, day, ["GPS KMs"])
        veh = veh.merge(u, on="Vehicle Number", how="left")
        veh = veh.merge(o, on="Vehicle Number", how="left")
        veh = veh.merge(g, on="Vehicle Number", how="left")

        for col in (
            "Revenue",
            "Trip Completed Count",
            "Trip Distance",
            "Ola Customer Bill",
            "Ola Actual Kms",
            "Ola Trips",
            "GPS KMs",
        ):
            if col not in veh.columns:
                veh[col] = 0
            veh[col] = veh[col].fillna(0)

        total_trip = (veh["Trip Completed Count"] + veh["Ola Trips"]).astype(int)
        intrip = (veh["Trip Distance"] + veh["Ola Actual Kms"]).round(2)
        total_rev = (veh["Revenue"] + veh["Ola Customer Bill"]).round(2)
        # Single day → Ideal = 30 + 3×trips + intrip
        ideal = (30 + (3 * total_trip) + intrip).round(2)
        buffer_km = (veh["GPS KMs"] - ideal).round(2)
        charges = (buffer_km.clip(lower=0) * 3).round(2)
        has_real_id = veh["Partner ID"].map(_is_real_partner_id)
        dead_type = pd.Series("Deadmile", index=veh.index, dtype=object)
        dead_type.loc[~has_real_id] = "Unproductive"

        frames.append(
            pd.DataFrame(
                {
                    "Date": [day, day, day],
                    "Metric": categories,
                    "Amount": [
                        float(total_rev.sum()),
                        float(charges[dead_type == "Unproductive"].sum()),
                        float(charges[dead_type == "Deadmile"].sum()),
                    ],
                }
            )
        )

    if frames:
        daily = pd.concat(frames, ignore_index=True)
    else:
        daily = pd.DataFrame(columns=["Date", "Metric", "Amount"])

    grid = pd.MultiIndex.from_product(
        [all_days, categories], names=["Date", "Metric"]
    ).to_frame(index=False)
    daily = grid.merge(daily, on=["Date", "Metric"], how="left")
    daily["Amount"] = daily["Amount"].fillna(0).round(2)
    daily["Date"] = daily["Date"].dt.strftime("%Y-%m-%d")
    return daily.reset_index(drop=True)


def _agg_metric_days(
    days: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    value_cols: list[str],
) -> pd.DataFrame:
    if days is None or days.empty:
        return pd.DataFrame(columns=["Vehicle Number", *value_cols])
    work = days[(days["Date"] >= start) & (days["Date"] <= end)]
    if work.empty:
        return pd.DataFrame(columns=["Vehicle Number", *value_cols])
    return (
        work.groupby("Vehicle Number", as_index=False)[value_cols]
        .sum()
    )


def assemble_fleet_table(
    start_date: str,
    end_date: str,
    alloc: pd.DataFrame,
    alloc_meta: dict,
    uber_days: pd.DataFrame,
    ola_days: pd.DataFrame,
    gps_days: pd.DataFrame | None = None,
    rapido_days: pd.DataFrame | None = None,
    *,
    write_output: bool = False,
    uber_base: Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Fast in-memory join for a date range from preloaded day tables."""
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        start, end = end, start
        start_date, end_date = end_date, start_date

    in_range = (
        alloc[(alloc["Date"] >= start) & (alloc["Date"] <= end)].copy()
        if not alloc.empty
        else alloc
    )
    day_rows = len(in_range)

    if in_range.empty:
        return _empty_table(), {
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "on_date": start.strftime("%Y-%m-%d"),
            "allocation": alloc_meta,
            "allocation_rows_on_date": 0,
            "vehicles": 0,
            "with_partner_id": 0,
            "with_uber": 0,
            "with_ola": 0,
            "needs_current_export": True,
            "base": "allocation_vehicles_then_uber_ola",
        }

    in_range = in_range.sort_values(["Vehicle Number", "Date"])
    lookup = in_range.groupby("Vehicle Number", as_index=False).tail(1).copy()
    lookup = lookup.rename(
        columns={
            "partner IDs": "Partner ID",
            "partner Name": "Partner Name",
        }
    )

    for col in ("Partner ID", "Partner Name", "DM Name", "Type", "City"):
        if col not in lookup.columns:
            lookup[col] = ""
        lookup[col] = (
            lookup[col]
            .fillna("")
            .astype(str)
            .str.strip()
            .replace({"nan": "", "None": "", "NaT": ""})
        )
        lookup[col] = lookup[col].where(
            ~lookup[col].str.lower().isin(["nan", "none", "nat"]), ""
        )
    # Same trim as Pan India / all datasets — required for Type Of Plan key
    lookup["Partner ID"] = lookup["Partner ID"].map(_norm_partner_id)
    lookup["Vehicle Number"] = lookup["Vehicle Number"].map(_norm_vehicle)

    id_by_name: dict[str, str] = {}
    name_by_id: dict[str, str] = {}
    for _, r in in_range.iterrows():
        pid = _norm_partner_id(r.get("partner IDs", ""))
        pname = str(r.get("partner Name", "") or "").strip()
        if pid and pname and pname.lower() not in {"nan", "none"}:
            id_by_name.setdefault(pname.upper(), pid)
            name_by_id.setdefault(pid.upper(), pname)
    blank_id = lookup["Partner ID"].eq("") & lookup["Partner Name"].ne("")
    lookup.loc[blank_id, "Partner ID"] = lookup.loc[blank_id, "Partner Name"].map(
        lambda n: id_by_name.get(str(n).upper(), "")
    )
    blank_name = lookup["Partner Name"].eq("") & lookup["Partner ID"].ne("")
    lookup.loc[blank_name, "Partner Name"] = lookup.loc[blank_name, "Partner ID"].map(
        lambda i: name_by_id.get(str(i).upper(), "")
    )

    if "Allocation Date" in lookup.columns:
        lookup["Latest Allocation Date"] = pd.to_datetime(
            lookup["Allocation Date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
    else:
        lookup["Latest Allocation Date"] = ""
    lookup["Latest Allocation Date"] = (
        lookup["Latest Allocation Date"].replace({"NaT": ""}).fillna("")
    )

    if "Drop Off Date" in lookup.columns:
        row_drop = pd.to_datetime(lookup["Drop Off Date"], errors="coerce")
    else:
        row_drop = pd.Series(pd.NaT, index=lookup.index)
    lookup["Drop Off Date"] = ""
    no_partner = lookup["Partner ID"].eq("")
    lookup.loc[no_partner, "Drop Off Date"] = (
        row_drop.loc[no_partner].dt.strftime("%Y-%m-%d").replace({"NaT": ""}).fillna("")
    )

    # Keep the allocation status day used for Partner ID (last day in range)
    lookup["Date"] = pd.to_datetime(lookup["Date"], errors="coerce").dt.strftime(
        "%Y-%m-%d"
    )
    lookup["Date"] = lookup["Date"].replace({"NaT": ""}).fillna("")
    # If blank, fall back to range end (selected End filter)
    blank_date = lookup["Date"].eq("")
    lookup.loc[blank_date, "Date"] = end.strftime("%Y-%m-%d")

    summary = _agg_metric_days(
        uber_days,
        start,
        end,
        ["Revenue", "Cash Collection", "Trip Completed Count", "Trip Distance"],
    )
    ola_summary = _agg_metric_days(
        ola_days,
        start,
        end,
        [
            "Ola Customer Bill",
            "Ola Cash Collected",
            "Ola Actual Kms",
            "Ola Trip Time",
            "Ola Trips",
        ],
    )
    gps_summary = _agg_metric_days(
        gps_days if gps_days is not None else pd.DataFrame(),
        start,
        end,
        ["GPS KMs"],
    )
    rapido_summary = _agg_metric_days(
        rapido_days if rapido_days is not None else pd.DataFrame(),
        start,
        end,
        ["Rapido Revenue", "Rapido Ride Time", "Rapido Trips"],
    )

    joined = lookup.merge(summary, on="Vehicle Number", how="left")
    joined = joined.merge(ola_summary, on="Vehicle Number", how="left")
    joined = joined.merge(gps_summary, on="Vehicle Number", how="left")
    joined = joined.merge(rapido_summary, on="Vehicle Number", how="left")

    for col in (
        "Revenue",
        "Cash Collection",
        "Trip Completed Count",
        "Trip Distance",
        "Ola Customer Bill",
        "Ola Cash Collected",
        "Ola Actual Kms",
        "Ola Trip Time",
        "Ola Trips",
        "GPS KMs",
        "Rapido Revenue",
        "Rapido Ride Time",
        "Rapido Trips",
    ):
        if col not in joined.columns:
            joined[col] = 0
        joined[col] = joined[col].fillna(0)

    for col in (
        "Partner ID",
        "Partner Name",
        "Latest Allocation Date",
        "Drop Off Date",
        "Type",
        "DM Name",
        "City",
    ):
        if col not in joined.columns:
            joined[col] = ""
        joined[col] = joined[col].fillna("")

    joined["Trip Completed Count"] = joined["Trip Completed Count"].astype(int)
    joined["Ola Trips"] = joined["Ola Trips"].astype(int)
    joined["Rapido Trips"] = joined["Rapido Trips"].astype(int)
    joined["Ola Trip Time"] = joined["Ola Trip Time"].round(0).astype(int)
    joined["Revenue"] = joined["Revenue"].round(2)
    joined["Cash Collection"] = joined["Cash Collection"].round(2)
    joined["Trip Distance"] = joined["Trip Distance"].round(2)
    joined["Ola Customer Bill"] = joined["Ola Customer Bill"].round(2)
    joined["Ola Cash Collected"] = joined["Ola Cash Collected"].round(2)
    joined["Ola Actual Kms"] = joined["Ola Actual Kms"].round(2)
    joined["GPS KMs"] = joined["GPS KMs"].round(2)
    joined["Rapido Revenue"] = joined["Rapido Revenue"].round(2)
    joined["Rapido Ride Time"] = joined["Rapido Ride Time"].round(2)
    joined["Total Revenue"] = (
        joined["Revenue"] + joined["Ola Customer Bill"] + joined["Rapido Revenue"]
    ).round(2)
    joined["Total Cash Collection"] = (
        joined["Cash Collection"] + joined["Ola Cash Collected"]
    ).round(2)
    joined["Total Trip"] = (
        joined["Trip Completed Count"] + joined["Ola Trips"] + joined["Rapido Trips"]
    ).astype(int)
    joined["Total Intrip KM"] = (
        joined["Trip Distance"] + joined["Ola Actual Kms"]
    ).round(2)
    # Ideal KM = 30 km buffer per day + 3 km per trip + Total Intrip KM
    days_in_range = int((end - start).days) + 1
    joined["Ideal KM"] = (
        (30 * days_in_range) + (3 * joined["Total Trip"]) + joined["Total Intrip KM"]
    ).round(2)
    # Approved KM = min(GPS, Ideal) − Intrip
    # (if GPS > Ideal → Ideal − Intrip; else GPS − Intrip)
    joined["Approved KM"] = (
        joined[["GPS KMs", "Ideal KM"]].min(axis=1) - joined["Total Intrip KM"]
    ).round(2)
    joined["Buffer KM"] = (joined["GPS KMs"] - joined["Ideal KM"]).round(2)
    joined["Deadmile Charges"] = (joined["Buffer KM"].clip(lower=0) * 3).round(2)
    # Dead KM = GPS − Ideal; never below 0
    joined["Dead KM"] = (joined["GPS KMs"] - joined["Ideal KM"]).clip(lower=0).round(2)
    # No Partner ID → Unproductive; else Deadmile
    has_real_id = joined["Partner ID"].map(_is_real_partner_id)
    joined["Dead KM Type"] = "Deadmile"
    joined.loc[~has_real_id, "Dead KM Type"] = "Unproductive"

    uber_on = (joined["Trip Completed Count"] > 0) | (joined["Revenue"] != 0)
    ola_on = (joined["Ola Trips"] > 0) | (joined["Ola Customer Bill"] != 0)
    rapido_on = (joined["Rapido Trips"] > 0) | (joined["Rapido Revenue"] != 0)

    def _run_on_label(u: bool, o: bool, r: bool) -> str:
        n = int(u) + int(o) + int(r)
        if n >= 2:
            return "Mix"
        if o:
            return "OLA"
        if u:
            return "Uber"
        if r:
            return "Rapido"
        return ""

    joined["Run On"] = [
        _run_on_label(bool(u), bool(o), bool(r))
        for u, o, r in zip(uber_on.tolist(), ola_on.tolist(), rapido_on.tolist())
    ]

    joined["Ageing"] = _assign_ageing(joined["Partner ID"], joined["Total Revenue"])

    # Type Of Plan from Pan India Allocation:
    # Vehicle + Partner ID → closest past Date Of Allocation (<= row Date)
    plan_meta: dict = {}
    try:
        from lib.pan_india_allocation import attach_type_of_plan

        joined, plan_meta = attach_type_of_plan(joined, as_of_date=end)
    except Exception as exc:
        joined["Type Of Plan"] = ""
        plan_meta = {"message": f"Type Of Plan failed: {exc}", "matched": 0, "pan_rows": 0}
    if "Type Of Plan" not in joined.columns:
        joined["Type Of Plan"] = ""
    joined["Type Of Plan"] = joined["Type Of Plan"].fillna("").astype(str)
    if "Date" not in joined.columns:
        joined["Date"] = end.strftime("%Y-%m-%d")
    joined["Date"] = joined["Date"].fillna(end.strftime("%Y-%m-%d")).astype(str)

    joined = (
        joined[TABLE_COLS]
        .sort_values(["Date", "Partner Name", "Vehicle Number"], ascending=True)
        .reset_index(drop=True)
    )

    out_path = ""
    if write_output and uber_base is not None:
        out_dir = Path(uber_base) / "Output"
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = (
            start.strftime("%Y-%m-%d")
            if start == end
            else f"{start.strftime('%Y-%m-%d')}_to_{end.strftime('%Y-%m-%d')}"
        )
        out_path = out_dir / f"Allocation_Uber_{tag}.csv"
        try:
            joined.to_csv(out_path, index=False)
        except PermissionError:
            out_path = out_dir / f"Allocation_Uber_{tag}_latest.csv"
            joined.to_csv(out_path, index=False)
        out_path = str(out_path)

    meta = {
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "on_date": start.strftime("%Y-%m-%d"),
        "allocation": alloc_meta,
        "allocation_rows_on_date": day_rows,
        "vehicles": len(joined),
        "with_partner_id": int(joined["Partner ID"].ne("").sum()),
        "with_uber": int(
            ((joined["Trip Completed Count"] > 0) | (joined["Revenue"] != 0)).sum()
        )
        if len(joined)
        else 0,
        "with_ola": int(
            ((joined["Ola Trips"] > 0) | (joined["Ola Customer Bill"] != 0)).sum()
        )
        if len(joined)
        else 0,
        "with_rapido": int(
            ((joined["Rapido Trips"] > 0) | (joined["Rapido Revenue"] != 0)).sum()
        )
        if len(joined)
        else 0,
        "with_gps": int((joined["GPS KMs"] > 0).sum()) if len(joined) else 0,
        "type_of_plan": plan_meta,
        "output_path": out_path,
        "needs_current_export": day_rows == 0,
        "base": "allocation_vehicles_then_uber_ola_rapido_gps",
    }
    return joined, meta


def build_uber_range_with_allocation(
    start_date: str,
    end_date: str,
    project_root: Path | None = None,
    *,
    write_output: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """
    Base = Excel vehicles that appear on any day in [start_date, end_date].
    Partner fields = last exact-day row in that range per vehicle.
    Uber/Ola/Rapido metrics = summed across the range for those vehicles.
    """
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
    uber_base = root / "Uber"
    alloc_base = root / "Vehicle Allocation Status"

    alloc, alloc_meta = load_allocation(alloc_base)
    uber_days, _ = build_uber_vehicle_days(uber_base)
    ola_days, _ = build_ola_vehicle_days(ola_dir(root))
    gps_days, _ = build_gps_vehicle_days(gps_dir(root))
    rapido_days, _ = build_rapido_vehicle_days(rapido_dir(root))
    return assemble_fleet_table(
        start_date,
        end_date,
        alloc,
        alloc_meta,
        uber_days,
        ola_days,
        gps_days,
        rapido_days,
        write_output=write_output,
        uber_base=uber_base,
    )
