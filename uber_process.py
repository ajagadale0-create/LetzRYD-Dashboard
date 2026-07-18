"""Uber PT + Trip processing: vehicle match and vehicle-wise summary.

Uber business day = Drop-off time with 4am→4am rule.
Example: file window 15–16 → trips before next-day 04:00 count as date 15.
Ola / Rapido do NOT use this rule (calendar date as-is in their loaders).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pandas as pd

CASH_COL = "Paid to you : Trip balance : Payouts : Cash collected"
FARE_COL = "Paid to you : Your earnings : Fare"
TOLL_COL = "Paid to you:Trip balance:Refunds:Toll"
TOLL_ADJ_COL = "Paid to you:Trip balance:Expenses:Toll adjustment"
TIP_COL = "Paid to you:Your earnings:Tip"

PT_USECOLS = [
    "transaction UUID",
    "Driver UUID",
    "Trip UUID",
    "vs reporting",
    CASH_COL,
    FARE_COL,
    TOLL_COL,
    TOLL_ADJ_COL,
    TIP_COL,
]
TRIP_USECOLS = [
    "Trip UUID",
    "Driver UUID",
    "Number plate",
    "Trip drop-off time",
    "Trip status",
    "Trip distance",
]


def _parse_date(value) -> str:
    """Calendar date YYYY-MM-DD (midnight boundary). For OLA/Rapido later."""
    if pd.isna(value) or not str(value).strip():
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", str(value).strip())
    return match.group(1) if match else ""


def uber_business_date(value) -> str:
    """Uber ops day = 04:00 → next day 04:00 (Drop-off / vs reporting).

    So a drop-off at 2026-07-16 03:30 belongs to business date 2026-07-15.
    File window 15–16 → date column = 15.
    """
    if pd.isna(value) or not str(value).strip():
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+[+-]\d{4}\s+\w+$", "", text)
    text = re.sub(r"\s+IST$", "", text, flags=re.I)
    ts = pd.to_datetime(text, errors="coerce")
    if pd.isna(ts):
        return _parse_date(value)
    return (ts - pd.Timedelta(hours=4)).strftime("%Y-%m-%d")


def uber_business_date_series(series: pd.Series) -> pd.Series:
    """Vectorized 4am→4am Uber business day (fast path for large CSVs)."""
    text = series.fillna("").astype(str).str.strip()
    text = text.str.replace(r"\s+[+-]\d{4}\s+\w+$", "", regex=True)
    text = text.str.replace(r"\s+IST$", "", regex=True, flags=re.I)
    ts = pd.to_datetime(text, errors="coerce")
    out = (ts - pd.Timedelta(hours=4)).dt.strftime("%Y-%m-%d")
    return out.where(ts.notna(), "").fillna("")


def list_csvs(folder: Path) -> list[Path]:
    files = sorted(folder.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No CSV found in {folder}")
    return files


def _read_csv_usecols(path: Path, wanted: list[str]) -> pd.DataFrame:
    header = list(pd.read_csv(path, nrows=0).columns)
    usecols = [c for c in wanted if c in header]
    return pd.read_csv(path, dtype=str, usecols=usecols)


def _read_concat_csvs(folder: Path, wanted: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Read every CSV in folder (only needed columns) and stack rows."""
    frames: list[pd.DataFrame] = []
    names: list[str] = []
    for path in list_csvs(folder):
        frames.append(_read_csv_usecols(path, wanted))
        names.append(path.name)
    return pd.concat(frames, ignore_index=True), names


def load_uber_data(base: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Load ALL PT and Trip CSVs (e.g. month dump in one shot)."""
    root = uber_root(base)
    pt, pt_files = _read_concat_csvs(root / "PT", PT_USECOLS)
    trip, trip_files = _read_concat_csvs(root / "Trip", TRIP_USECOLS)

    if "transaction UUID" in pt.columns:
        pt = pt.drop_duplicates(subset=["transaction UUID"], keep="last")
    if "Trip UUID" in trip.columns:
        trip = trip.drop_duplicates(subset=["Trip UUID"], keep="last")

    meta = {
        "pt_files": pt_files,
        "trip_files": trip_files,
        "pt_file": ", ".join(pt_files),
        "trip_file": ", ".join(trip_files),
    }
    return pt, trip, meta


def load_uber_paths(base: Path | None = None) -> tuple[Path, Path]:
    """Backward-compatible: returns first file of each folder (prefer load_uber_data)."""
    root = uber_root(base)
    return list_csvs(root / "PT")[0], list_csvs(root / "Trip")[0]


def uber_root(base: Path | None = None) -> Path:
    return Path(base) if base else Path(__file__).resolve().parents[1] / "Uber"


def assign_vehicle_numbers(pt: pd.DataFrame, trip: pd.DataFrame) -> pd.DataFrame:
    """Attach Vehicle Number to PT using Trip UUID → date+driver → driver only."""
    pt = pt.copy()
    trip = trip.copy()

    for frame in (pt, trip):
        for col in ("Trip UUID", "Driver UUID"):
            if col in frame.columns:
                frame[col] = frame[col].fillna("").astype(str).str.strip()

    trip["Number plate"] = trip["Number plate"].fillna("").astype(str).str.strip()
    # Match keys use Uber 4am day (Drop-off on Trip; vs reporting fallback on PT)
    pt["match_date"] = uber_business_date_series(pt["vs reporting"])
    trip["match_date"] = uber_business_date_series(trip["Trip drop-off time"])

    trip_uuid_map = (
        trip[(trip["Trip UUID"] != "") & (trip["Number plate"] != "")]
        .drop_duplicates("Trip UUID", keep="first")
        .set_index("Trip UUID")["Number plate"]
    )

    combo = trip[
        (trip["Driver UUID"] != "")
        & (trip["Number plate"] != "")
        & (trip["match_date"] != "")
    ]
    combo_best = (
        combo.groupby(["match_date", "Driver UUID", "Number plate"], as_index=False)
        .size()
        .sort_values("size", ascending=False)
        .drop_duplicates(["match_date", "Driver UUID"], keep="first")
        .rename(columns={"Number plate": "_v2"})
    )
    drv_best = (
        combo.groupby(["Driver UUID", "Number plate"], as_index=False)
        .size()
        .sort_values("size", ascending=False)
        .drop_duplicates(["Driver UUID"], keep="first")
        .set_index("Driver UUID")["Number plate"]
    )

    v1 = pt["Trip UUID"].map(trip_uuid_map).fillna("")
    pt = pt.merge(
        combo_best[["match_date", "Driver UUID", "_v2"]],
        on=["match_date", "Driver UUID"],
        how="left",
    )
    v2 = pt["_v2"].fillna("")
    v3 = pt["Driver UUID"].map(drv_best).fillna("")

    vehicle = v1.where(v1.ne(""), v2.where(v2.ne(""), v3))
    method = pd.Series("unmatched", index=pt.index)
    method = method.mask(v3.ne("") & v1.eq("") & v2.eq(""), "3_driver_only")
    method = method.mask(v2.ne("") & v1.eq(""), "2_date_driver")
    method = method.mask(v1.ne(""), "1_trip_uuid")

    pt["Vehicle Number"] = vehicle
    pt["vehicle_match_method"] = method
    pt = pt.drop(columns=["_v2"], errors="ignore")
    return pt


def _prepared_pt_trip(base: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Load PT+Trip, attach vehicles; Uber biz date = Drop-off 4am (fallback vs reporting)."""
    pt, trip, file_meta = load_uber_data(base)

    for col in (CASH_COL, FARE_COL, TOLL_COL, TOLL_ADJ_COL, TIP_COL):
        if col not in pt.columns:
            # optional money cols — fill 0 if missing in some exports
            pt[col] = "0"

    for col in (CASH_COL, FARE_COL, TOLL_COL, TOLL_ADJ_COL, TIP_COL):
        pt[col] = pd.to_numeric(pt[col], errors="coerce").fillna(0.0)

    pt["Cash Collection"] = pt[CASH_COL].abs()
    pt["Revenue"] = pt[FARE_COL] + pt[TOLL_COL] + pt[TOLL_ADJ_COL] + pt[TIP_COL]

    pt = assign_vehicle_numbers(pt, trip)
    pt["Vehicle Number"] = (
        pt["Vehicle Number"].fillna("").astype(str).str.upper().str.replace(r"\s+", "", regex=True)
    )

    trip = trip.copy()
    trip["Trip UUID"] = trip["Trip UUID"].fillna("").astype(str).str.strip()
    trip["Trip status"] = trip["Trip status"].fillna("").astype(str).str.strip().str.lower()
    trip["Trip distance"] = pd.to_numeric(trip["Trip distance"], errors="coerce").fillna(0.0)
    if "match_date" not in trip.columns:
        trip["match_date"] = uber_business_date_series(trip["Trip drop-off time"])

    trip_lookup = (
        trip[(trip["Trip UUID"] != "")]
        .drop_duplicates("Trip UUID", keep="first")[
            ["Trip UUID", "Trip distance", "Trip status", "match_date"]
        ]
        .rename(
            columns={
                "Trip distance": "_trip_distance",
                "Trip status": "_trip_status",
                "match_date": "_drop_biz_date",
            }
        )
    )

    pt["Trip UUID"] = pt["Trip UUID"].fillna("").astype(str).str.strip()
    pt = pt.merge(trip_lookup, on="Trip UUID", how="left")

    # PRIMARY Uber date = Trip Drop-off (4am→4am). Fallback = PT vs reporting (same 4am rule).
    drop_biz = pt["_drop_biz_date"].fillna("").astype(str)
    vs_biz = pt["match_date"].fillna("").astype(str)
    pt["biz_date"] = drop_biz.where(drop_biz.ne(""), vs_biz)

    return pt, trip, file_meta


def _cache_dir(base: Path | None = None) -> Path:
    path = uber_root(base) / "Output" / ".cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_uber_vehicle_days(base: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Vehicle × Uber business day metrics (Drop-off 4am day). Cached on disk."""
    fp = source_fingerprint(base)
    cache_key = hashlib.md5(f"v2-dropoff-4am|{fp}".encode("utf-8")).hexdigest()[:16]
    cache_path = _cache_dir(base) / f"uber_vehicle_days_{cache_key}.parquet"
    meta_path = _cache_dir(base) / f"uber_vehicle_days_{cache_key}.json"

    if cache_path.exists() and meta_path.exists():
        try:
            import json

            days = pd.read_parquet(cache_path)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return days, meta
        except Exception:
            pass
    pkl_path = cache_path.with_suffix(".pkl")
    if pkl_path.exists() and meta_path.exists():
        try:
            import json

            days = pd.read_pickle(pkl_path)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return days, meta
        except Exception:
            pass

    pt, trip, file_meta = _prepared_pt_trip(base)
    pt_veh = pt[(pt["Vehicle Number"] != "") & (pt["biz_date"].fillna("") != "")].copy()

    pt_agg = pt_veh.groupby(["Vehicle Number", "biz_date"], as_index=False).agg(
        **{
            "Cash Collection": ("Cash Collection", "sum"),
            "Revenue": ("Revenue", "sum"),
        }
    )

    trip_lines = pt_veh[
        (pt_veh["Trip UUID"] != "")
        & (pt_veh["_trip_status"].fillna("").str.lower() == "completed")
    ][["Vehicle Number", "biz_date", "Trip UUID", "_trip_distance"]].drop_duplicates(
        ["Vehicle Number", "biz_date", "Trip UUID"]
    )
    trip_agg = trip_lines.groupby(["Vehicle Number", "biz_date"], as_index=False).agg(
        **{
            "Trip Completed Count": ("Trip UUID", "count"),
            "Trip Distance": ("_trip_distance", "sum"),
        }
    )

    days = pd.merge(
        pt_agg, trip_agg, on=["Vehicle Number", "biz_date"], how="left"
    ).fillna(0)
    days = days.rename(columns={"biz_date": "Date"})
    days["Date"] = pd.to_datetime(days["Date"], errors="coerce").dt.normalize()
    days["Trip Completed Count"] = days["Trip Completed Count"].astype(int)
    days["Cash Collection"] = days["Cash Collection"].round(2)
    days["Revenue"] = days["Revenue"].round(2)
    days["Trip Distance"] = days["Trip Distance"].round(2)
    days = days[
        [
            "Vehicle Number",
            "Date",
            "Revenue",
            "Cash Collection",
            "Trip Completed Count",
            "Trip Distance",
        ]
    ].sort_values(["Date", "Vehicle Number"]).reset_index(drop=True)

    meta = {
        "pt_files": file_meta["pt_files"],
        "trip_files": file_meta["trip_files"],
        "rows": len(days),
        "vehicles": int(days["Vehicle Number"].nunique()) if len(days) else 0,
        "date_from": days["Date"].min().strftime("%Y-%m-%d") if len(days) else "",
        "date_to": days["Date"].max().strftime("%Y-%m-%d") if len(days) else "",
        "day_rule": "uber_dropoff_4am",
        "date_source": "Trip Drop-off (4am→4am); fallback PT vs reporting",
        "cached": False,
    }

    try:
        import json

        try:
            days.to_parquet(cache_path, index=False)
        except Exception:
            cache_path = cache_path.with_suffix(".pkl")
            days.to_pickle(cache_path)
        meta_path.write_text(json.dumps({**meta, "cached": True}, indent=2), encoding="utf-8")
    except Exception:
        pass

    return days, meta


def build_vehicle_summary(
    pt_path: Path | None = None,
    trip_path: Path | None = None,
    on_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    base: Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Build vehicle table from PT, enriched with Trip (Drop-off 4am dates)."""
    _ = (pt_path, trip_path)
    days, day_meta = build_uber_vehicle_days(base)
    empty = pd.DataFrame(
        columns=[
            "Vehicle Number",
            "Revenue",
            "Cash Collection",
            "Trip Completed Count",
            "Trip Distance",
        ]
    )
    if days.empty:
        return empty, {**day_meta, "vehicles": 0, "on_date": on_date or ""}

    work = days
    if on_date:
        d = pd.Timestamp(on_date).normalize()
        work = work[work["Date"] == d]
    elif date_from or date_to:
        d0 = pd.Timestamp(date_from or "1900-01-01").normalize()
        d1 = pd.Timestamp(date_to or "2100-12-31").normalize()
        work = work[(work["Date"] >= d0) & (work["Date"] <= d1)]

    if work.empty:
        return empty, {
            **day_meta,
            "vehicles": 0,
            "date_from": on_date or (date_from or ""),
            "date_to": on_date or (date_to or ""),
            "on_date": on_date or "",
        }

    summary = (
        work.groupby("Vehicle Number", as_index=False)
        .agg(
            **{
                "Revenue": ("Revenue", "sum"),
                "Cash Collection": ("Cash Collection", "sum"),
                "Trip Completed Count": ("Trip Completed Count", "sum"),
                "Trip Distance": ("Trip Distance", "sum"),
            }
        )
    )
    summary["Trip Completed Count"] = summary["Trip Completed Count"].astype(int)
    summary["Cash Collection"] = summary["Cash Collection"].round(2)
    summary["Revenue"] = summary["Revenue"].round(2)
    summary["Trip Distance"] = summary["Trip Distance"].round(2)
    summary = summary.sort_values("Revenue", ascending=False).reset_index(drop=True)

    meta = {
        "pt_file": ", ".join(day_meta.get("pt_files", [])),
        "trip_file": ", ".join(day_meta.get("trip_files", [])),
        "pt_files": day_meta.get("pt_files", []),
        "trip_files": day_meta.get("trip_files", []),
        "pt_rows": int(len(work)),
        "trip_rows": 0,
        "matched_pt": int(len(work)),
        "vehicles": len(summary),
        "date_from": on_date or (date_from or day_meta.get("date_from", "")),
        "date_to": on_date or (date_to or day_meta.get("date_to", "")),
        "on_date": on_date or "",
        "date_source": day_meta.get(
            "date_source", "Trip Drop-off (4am→4am); fallback PT vs reporting"
        ),
        "day_rule": "uber_dropoff_4am",
        "pipeline": "Trip Drop-off date + PT money; Ola/Rapido calendar date",
        "fingerprint": source_fingerprint(uber_root(base)),
    }
    return summary, meta


def available_pt_business_dates(base: Path | None = None) -> list[str]:
    """Uber business dates from Trip Drop-off (4am) + PT vs reporting fallback."""
    root = uber_root(base)
    dates: set[str] = set()
    try:
        for path in list_csvs(root / "Trip"):
            series = pd.read_csv(path, dtype=str, usecols=["Trip drop-off time"])[
                "Trip drop-off time"
            ]
            dates.update(d for d in uber_business_date_series(series).tolist() if d)
    except Exception:
        pass
    try:
        for path in list_csvs(root / "PT"):
            series = pd.read_csv(path, dtype=str, usecols=["vs reporting"])["vs reporting"]
            dates.update(d for d in uber_business_date_series(series).tolist() if d)
    except Exception:
        pass
    return sorted(dates)


def source_fingerprint(base: Path | None = None) -> str:
    """Stable signature of all PT/Trip CSVs (name + size; skip mtime — Drive flickers)."""
    root = uber_root(base)
    parts: list[str] = []
    for folder in (root / "PT", root / "Trip"):
        if not folder.exists():
            parts.append(f"{folder.name}:missing")
            continue
        for path in sorted(folder.glob("*.csv")):
            try:
                parts.append(f"{path.name}:{path.stat().st_size}")
            except OSError:
                parts.append(f"{path.name}:missing")
    return "|".join(parts) if parts else "empty"


def output_paths(base: Path | None = None) -> tuple[Path, Path]:
    out_dir = uber_root(base) / "Output"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "Vehicle_wise_Summary.csv", out_dir / "last_refresh.json"


def refresh_and_save(base: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Rebuild vehicle summary and write Output files. Safe to call from watcher."""
    import json
    import os
    import tempfile
    from datetime import datetime

    root = uber_root(base)
    summary, meta = build_vehicle_summary(base=root)

    summary_path, meta_path = output_paths(root)

    def _atomic_csv(path: Path) -> Path:
        fd, tmp_name = tempfile.mkstemp(prefix="veh_", suffix=".csv", dir=str(path.parent))
        os.close(fd)
        tmp_path = Path(tmp_name)
        summary.to_csv(tmp_path, index=False)
        try:
            os.replace(tmp_path, path)
            return path
        except PermissionError:
            fallback = path.with_name(path.stem + "_latest.csv")
            try:
                os.replace(tmp_path, fallback)
            except Exception:
                summary.to_csv(fallback, index=False)
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            return fallback

    written = _atomic_csv(summary_path)

    payload = {
        **meta,
        "refreshed_at": datetime.now().isoformat(timespec="seconds"),
        "summary_rows": len(summary),
        "summary_path": str(written),
    }
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return summary, payload
