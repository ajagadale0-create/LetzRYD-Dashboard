"""Pan India Allocation — Type Of Plan by Vehicle + Operator/Driver ID.

Required columns from live Google Sheet:
  - Date Of Allocation
  - Operator/Driver ID
  - Type Of Plan
  - Vehicle Number

Trick:
  key = Vehicle Number + Operator/Driver ID
  for a fleet row date D → take max Date Of Allocation <= D for that key
  → that row's Type Of Plan

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


def sync_pan_india_sheet(*, sheet_id: str | None = None, force: bool = False) -> dict:
    """Auto-export Pan India Allocation Google Sheet via service account."""
    _ = sheet_id
    try:
        from lib.drive_sync import sync_named_sheet_from_drive

        return sync_named_sheet_from_drive(
            name_hint="pan india allocation",
            dest_name=EXPORT_NAME,
            force=force,
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
    # Uppercase ID so LETZ… / letz… always match
    return f"{_norm_vehicle(vehicle)}|{_norm_partner_id(partner_id).upper()}"


def _pick_column(columns: list[str], *predicates) -> str | None:
    """Return first column whose lower name matches any predicate(fn)."""
    for col in columns:
        low = str(col).strip().casefold()
        for pred in predicates:
            if pred(low):
                return col
    return None


def _map_required_columns(df: pd.DataFrame) -> dict[str, str]:
    """Map raw sheet headers → required logical names."""
    cols = list(df.columns)
    mapping: dict[str, str] = {}

    date_col = _pick_column(
        cols,
        lambda s: "date" in s and "alloc" in s,
        lambda s: s in {"date of allocation", "allocation date"},
    )
    id_col = _pick_column(
        cols,
        lambda s: ("operator" in s or "driver" in s) and "id" in s,
        lambda s: s in {"partner id", "partner ids", "id"},
        lambda s: "operator/driver" in s,
    )
    plan_col = _pick_column(
        cols,
        lambda s: "type" in s and "plan" in s,
        lambda s: s in {"plan type", "plan", "type of plan"},
        lambda s: "plan" in s and "type" in s,
    )
    veh_col = _pick_column(
        cols,
        lambda s: "vehicle" in s and ("number" in s or "no" in s or "num" in s),
        lambda s: s in {"vehicle", "vehicle number", "vehicle no", "reg no", "registration"},
        lambda s: "vehicle" in s,
    )

    if date_col:
        mapping[date_col] = "Date Of Allocation"
    if id_col:
        mapping[id_col] = "Operator/Driver ID"
    if plan_col:
        mapping[plan_col] = "Type Of Plan"
    if veh_col:
        mapping[veh_col] = "Vehicle Number"
    return mapping


def _score_sheet(df: pd.DataFrame) -> int:
    return len(_map_required_columns(df))


def _read_pan_india_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        xl = pd.ExcelFile(path)
        best_df = None
        best_score = -1
        for sheet in xl.sheet_names:
            raw = pd.read_excel(path, sheet_name=sheet, dtype=str)
            score = _score_sheet(raw)
            if score > best_score:
                best_score = score
                best_df = raw
        if best_df is None:
            raise ValueError(f"No sheets in {path.name}")
        df = best_df
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
    else:
        raise ValueError(f"Unsupported Pan India file: {path.name}")

    mapping = _map_required_columns(df)
    required = [
        "Date Of Allocation",
        "Operator/Driver ID",
        "Type Of Plan",
        "Vehicle Number",
    ]
    mapped_vals = set(mapping.values())
    missing = [c for c in required if c not in mapped_vals]
    if missing:
        raise KeyError(
            f"{path.name} missing columns {missing}. "
            f"Found headers: {list(df.columns)}"
        )

    out = df.rename(columns=mapping)[required].copy()
    out["Vehicle Number"] = out["Vehicle Number"].map(_norm_vehicle)
    out["Operator/Driver ID"] = out["Operator/Driver ID"].map(
        lambda x: _norm_partner_id(x).upper()
    )
    out["Type Of Plan"] = out["Type Of Plan"].map(_normalize_text)
    out["Date Of Allocation"] = pd.to_datetime(
        out["Date Of Allocation"], errors="coerce", dayfirst=True
    ).dt.normalize()

    out = out[out["Vehicle Number"] != ""].copy()
    out = out[out["Operator/Driver ID"] != ""].copy()
    out = out[out["Date Of Allocation"].notna()].copy()
    out = out[out["Type Of Plan"] != ""].copy()

    out["_key"] = [
        _match_key(v, p)
        for v, p in zip(out["Vehicle Number"], out["Operator/Driver ID"])
    ]
    out = (
        out.sort_values(["_key", "Date Of Allocation"])
        .drop_duplicates(["_key", "Date Of Allocation"], keep="last")
        .reset_index(drop=True)
    )
    return out


def load_pan_india_allocation(
    folder: Path | None = None, *, force_sync: bool = False
) -> tuple[pd.DataFrame, dict]:
    folder = folder or pan_india_dir()
    sync_info = sync_pan_india_sheet(force=force_sync)
    files = list_pan_india_files(folder)
    if not files and not sync_info.get("ok"):
        # One forced retry — sheet may be new on Drive
        sync_info = sync_pan_india_sheet(force=True)
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
        "keys": int(df["_key"].nunique()) if len(df) else 0,
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
    as_of_date: str | pd.Timestamp | None = None,
    pan_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Add Type Of Plan using Vehicle + Operator/Driver ID + closest past Date Of Allocation.

    Trick:
      key = Vehicle Number | Operator/Driver ID
      for fleet date D → max(Date Of Allocation) where Date Of Allocation <= D
      → that Type Of Plan
    """
    out = fleet.copy()
    if "Type Of Plan" not in out.columns:
        out["Type Of Plan"] = ""

    meta = {
        "pan_rows": 0,
        "matched": 0,
        "message": "",
    }

    if out.empty:
        meta["message"] = "Fleet empty"
        return out, meta

    pan_meta: dict = {}
    if pan_df is None:
        pan_df, pan_meta = load_pan_india_allocation()
    if pan_df is None or pan_df.empty:
        meta["message"] = pan_meta.get("message") or "No Pan India Allocation data"
        if pan_meta.get("errors"):
            meta["errors"] = pan_meta["errors"]
        meta["sync"] = pan_meta.get("sync")
        return out, meta

    meta["pan_rows"] = len(pan_df)
    meta["loaded_file"] = pan_meta.get("loaded_file", "")

    fallback = (
        pd.Timestamp(as_of_date).normalize()
        if as_of_date is not None
        else pd.Timestamp.today().normalize()
    )
    if "Date" in out.columns:
        asof = pd.to_datetime(out["Date"], errors="coerce").fillna(fallback)
    else:
        asof = pd.Series(fallback, index=out.index)
    asof = pd.to_datetime(asof, errors="coerce").dt.normalize()

    left = pd.DataFrame(
        {
            "_row": out.index.astype(int),
            "_key": [
                _match_key(v, p)
                for v, p in zip(
                    out.get("Vehicle Number", pd.Series(dtype=str)).fillna(""),
                    out.get("Partner ID", pd.Series(dtype=str)).fillna(""),
                )
            ],
            "_asof": asof,
        }
    )
    left = left[left["_key"].ne("|") & left["_asof"].notna()].copy()

    right = pan_df[["_key", "Date Of Allocation", "Type Of Plan"]].copy()
    right["Date Of Allocation"] = pd.to_datetime(
        right["Date Of Allocation"], errors="coerce"
    ).dt.normalize()
    right = right[
        right["_key"].ne("|")
        & right["Date Of Allocation"].notna()
        & right["Type Of Plan"].fillna("").astype(str).str.strip().ne("")
    ].copy()

    if left.empty or right.empty:
        meta["message"] = "No valid Vehicle+ID keys to match Type Of Plan"
        return out, meta

    # Same Vehicle+ID → keep plans with Date Of Allocation <= row Date,
    # then pick highest past allocation date (avoids merge_asof sort errors)
    joined = left.merge(right, on="_key", how="left")
    joined = joined[
        joined["Date Of Allocation"].isna()
        | (joined["Date Of Allocation"] <= joined["_asof"])
    ].copy()
    if joined.empty:
        meta["message"] = (
            f"Pan India rows={meta['pan_rows']} · no past Date Of Allocation match"
        )
        return out, meta

    joined = joined.sort_values(["_row", "Date Of Allocation"], na_position="first")
    best = joined.groupby("_row", as_index=False).tail(1)
    plan_map = best.set_index("_row")["Type Of Plan"].fillna("").astype(str)
    out["Type Of Plan"] = out.index.map(lambda i: plan_map.get(i, "")).fillna("")
    out["Type Of Plan"] = (
        out["Type Of Plan"].astype(str).replace({"nan": "", "None": "", "NaT": ""})
    )
    meta["matched"] = int((out["Type Of Plan"].astype(str).str.strip() != "").sum())
    meta["message"] = (
        f"Pan India rows={meta['pan_rows']} · "
        f"Type Of Plan filled={meta['matched']}/{len(out)}"
    )
    return out, meta
