"""Pan India Allocation — Type Of Plan by Vehicle + Operator/Driver ID.

Match key = Vehicle Number + Operator/Driver ID.
For any as-of date (e.g. yesterday / selected End date):
  take the row with highest Date Of Allocation that is already on/before that date.

Data source: live Google Sheet auto-synced via service account (no manual Excel).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from lib.allocation import _norm_partner_id, _norm_vehicle
from lib.paths import code_root, data_root

SUPPORTED_SUFFIXES = {".xlsx", ".xls", ".csv"}
EXPORT_NAME = "Pan India Allocation.xlsx"
SHEET_NAME_HINT = "pan india allocation"


def pan_india_dir(base: Path | None = None) -> Path:
    if base is not None:
        return Path(base)
    return data_root() / "Allocation & Drop off form"


def sync_pan_india_sheet(*, sheet_id: str | None = None) -> dict:
    """Auto-export Pan India Allocation Google Sheet via service account."""
    _ = sheet_id
    try:
        from lib.drive_sync import sync_named_sheet_from_drive

        return sync_named_sheet_from_drive(
            name_hint="pan india allocation",
            dest_name=EXPORT_NAME,
            force=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Pan India auto-sync failed: {exc}",
            "bytes": 0,
            "path": "",
        }


def list_pan_india_files(folder: Path | None = None) -> list[Path]:
    folder = folder or pan_india_dir()
    candidates: list[Path] = []
    search_dirs = [folder]
    cache_dir = code_root() / ".data_cache" / "Allocation & Drop off form"
    try:
        if cache_dir.exists() and cache_dir.resolve() != Path(folder).resolve():
            search_dirs.append(cache_dir)
    except OSError:
        if cache_dir.exists():
            search_dirs.append(cache_dir)

    for d in search_dirs:
        if not d.exists():
            continue
        for p in d.iterdir():
            if not p.is_file() or p.name.startswith("~$"):
                continue
            if p.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            if SHEET_NAME_HINT in p.name.casefold():
                candidates.append(p)
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _normalize_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "nat", "-"} else text


def _match_key(vehicle: str, partner_id: str) -> str:
    return f"{_norm_vehicle(vehicle)}|{_norm_partner_id(partner_id)}"


def _read_pan_india_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path, dtype=str)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
    else:
        raise ValueError(f"Unsupported Pan India file: {path.name}")

    rename = {}
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    wanted = {
        "date of allocation": "Date Of Allocation",
        "allocation date": "Date Of Allocation",
        "operator/driver id": "Operator/Driver ID",
        "operator / driver id": "Operator/Driver ID",
        "operator driver id": "Operator/Driver ID",
        "driver id": "Operator/Driver ID",
        "partner id": "Operator/Driver ID",
        "partner ids": "Operator/Driver ID",
        "type of plan": "Type Of Plan",
        "plan type": "Type Of Plan",
        "vehicle number": "Vehicle Number",
        "vehicle no": "Vehicle Number",
        "vehicle": "Vehicle Number",
    }
    for key, canon in wanted.items():
        if key in lower_map:
            rename[lower_map[key]] = canon
    for raw_lower, raw_col in list(lower_map.items()):
        if raw_col in rename.values() or raw_col in rename:
            continue
        if "type" in raw_lower and "plan" in raw_lower:
            rename[raw_col] = "Type Of Plan"
        elif "date" in raw_lower and "alloc" in raw_lower:
            rename[raw_col] = "Date Of Allocation"
        elif ("operator" in raw_lower or "driver" in raw_lower) and "id" in raw_lower:
            rename[raw_col] = "Operator/Driver ID"
        elif "vehicle" in raw_lower and ("number" in raw_lower or "no" in raw_lower):
            rename[raw_col] = "Vehicle Number"
    df = df.rename(columns=rename)

    required = [
        "Date Of Allocation",
        "Operator/Driver ID",
        "Type Of Plan",
        "Vehicle Number",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(
            f"{path.name} missing columns: {missing}. Found: {list(df.columns)}"
        )

    out = df[required].copy()
    out["Vehicle Number"] = out["Vehicle Number"].map(_norm_vehicle)
    out["Operator/Driver ID"] = out["Operator/Driver ID"].map(_norm_partner_id)
    out["Type Of Plan"] = out["Type Of Plan"].map(_normalize_text)
    out["Date Of Allocation"] = pd.to_datetime(
        out["Date Of Allocation"], errors="coerce", dayfirst=True
    ).dt.normalize()
    out = out[out["Vehicle Number"] != ""].copy()
    out = out[out["Date Of Allocation"].notna()].copy()
    out["_key"] = [
        _match_key(v, p)
        for v, p in zip(out["Vehicle Number"], out["Operator/Driver ID"])
    ]
    out = out.sort_values(["_key", "Date Of Allocation"]).reset_index(drop=True)
    return out


def load_pan_india_allocation(folder: Path | None = None) -> tuple[pd.DataFrame, dict]:
    folder = folder or pan_india_dir()
    sync_info = sync_pan_india_sheet()
    files = list_pan_india_files(folder)
    if not files:
        return pd.DataFrame(), {
            "rows": 0,
            "loaded_file": "",
            "sync": sync_info,
            "message": (
                "Pan India Allocation auto-sync pending. "
                + str(sync_info.get("message") or "")
            ),
        }

    path = files[0]
    try:
        df = _read_pan_india_file(path)
    except Exception as exc:
        return pd.DataFrame(), {
            "rows": 0,
            "loaded_file": path.name,
            "sync": sync_info,
            "errors": [f"{path.name}: {exc}"],
            "message": "Could not read Pan India Allocation file.",
        }

    return df, {
        "rows": len(df),
        "loaded_file": path.name,
        "sync": sync_info,
        "date_from": (
            df["Date Of Allocation"].min().strftime("%Y-%m-%d") if len(df) else ""
        ),
        "date_to": (
            df["Date Of Allocation"].max().strftime("%Y-%m-%d") if len(df) else ""
        ),
        "message": "Loaded Pan India Allocation",
    }


def attach_type_of_plan(
    fleet: pd.DataFrame,
    as_of_date: str | pd.Timestamp,
    pan_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Add Type Of Plan to fleet rows.

    For each Vehicle Number + Partner ID:
      pick the Pan India row with max Date Of Allocation <= as_of_date.
    """
    out = fleet.copy()
    if "Type Of Plan" not in out.columns:
        out["Type Of Plan"] = ""

    if out.empty:
        return out

    if pan_df is None:
        pan_df, _ = load_pan_india_allocation()
    if pan_df is None or pan_df.empty:
        return out

    as_of = pd.Timestamp(as_of_date).normalize()
    hist = pan_df[pan_df["Date Of Allocation"] <= as_of].copy()
    if hist.empty:
        return out

    latest = (
        hist.sort_values(["_key", "Date Of Allocation"])
        .drop_duplicates("_key", keep="last")
        .loc[:, ["_key", "Type Of Plan"]]
    )

    keys = [
        _match_key(v, p)
        for v, p in zip(
            out.get("Vehicle Number", pd.Series(dtype=str)).fillna(""),
            out.get("Partner ID", pd.Series(dtype=str)).fillna(""),
        )
    ]
    out["_key"] = keys
    out = out.drop(columns=["Type Of Plan"], errors="ignore")
    out = out.merge(latest, on="_key", how="left")
    out["Type Of Plan"] = out["Type Of Plan"].fillna("").astype(str)
    out = out.drop(columns=["_key"])
    return out
