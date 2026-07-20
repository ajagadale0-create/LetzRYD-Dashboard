"""Pan India Allocation — Type Of Plan by Vehicle + Operator/Driver ID.

Required columns from live Google Sheet:
  - Date Of Allocation
  - Operator/Driver ID
  - Type Of Plan
  - Vehicle Number (header often Vehicle Num)

Trick (STRICT — no vehicle-only / id-only guess):
  key = Vehicle Number + Operator/Driver ID
  for a fleet row date D → take max Date Of Allocation <= D for that key
  → that row's Type Of Plan column only

If Type Of Plan cell is blank on a sheet row, Driver Plan on the SAME row
may fill it (never borrow plan from another vehicle or another partner).

Data source: live Google Sheet auto-synced via service account (no manual Excel).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from lib.allocation import _norm_partner_id
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


def _trim(value) -> str:
    """Aggressive trim for matching: spaces, NBSP, zero-width, tabs/newlines."""
    if pd.isna(value):
        return ""
    text = str(value)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]", "", text)
    text = text.strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _norm_vehicle_key(value) -> str:
    """KA-05-AN-9208 / ka 05 an 9208 → KA05AN9208 (trim + alphanum only)."""
    text = _trim(value).upper()
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text)


def _norm_id_key(value) -> str:
    """Trim + remove all internal spaces for Partner / Operator ID match."""
    text = _trim(value).upper()
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    if text.lower() in {"nan", "none", "null", "-", "rfd"}:
        return ""
    # Excel float leftovers: 9550473489.0
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    return _norm_partner_id(text).strip().upper()


def _normalize_text(value) -> str:
    """Trim plan labels (Type Of Plan / Driver Plan)."""
    text = _trim(value)
    return "" if text.lower() in {"nan", "none", "null", "nat", "-"} else text


def _match_key(vehicle: str, partner_id: str) -> str:
    """Match only on trimmed/normalized vehicle + id."""
    return f"{_norm_vehicle_key(vehicle)}|{_norm_id_key(partner_id)}"


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
    # Prefer Operator/Driver ID — never pick "Driver Plan"
    id_col = _pick_column(
        cols,
        lambda s: "operator" in s and "driver" in s and "id" in s,
        lambda s: ("operator" in s or "driver" in s) and "id" in s and "plan" not in s,
        lambda s: s in {"partner id", "partner ids"},
    )
    # Prefer "Type Of Plan" — never pick "Driver Plan"
    plan_col = _pick_column(
        cols,
        lambda s: "type" in s and "plan" in s,
        lambda s: s in {"plan type", "type of plan"},
    )

    def _is_vehicle_col(s: str) -> bool:
        if any(x in s for x in ("upload", "photo", "image", "file", "attach", "model")):
            return False
        if "vehicle" in s and any(x in s for x in ("num", "number", "no", "reg")):
            return True
        return s in {"vehicle number", "vehicle num", "vehicle no", "vehicle"}

    veh_col = _pick_column(
        cols,
        lambda s: s in {"vehicle number", "vehicle num", "vehicle no"},
        _is_vehicle_col,
    )

    if date_col:
        mapping[date_col] = "Date Of Allocation"
    if id_col:
        mapping[id_col] = "Operator/Driver ID"
    if plan_col:
        mapping[plan_col] = "Type Of Plan"
    if veh_col:
        mapping[veh_col] = "Vehicle Number"

    # Optional: same-row fill only when Type Of Plan cell blank
    driver_plan_col = _pick_column(
        cols,
        lambda s: s == "driver plan",
        lambda s: "driver" in s and "plan" in s and "type" not in s and "id" not in s,
    )
    if driver_plan_col and driver_plan_col not in mapping:
        mapping[driver_plan_col] = "Driver Plan"

    return mapping


def _score_sheet(df: pd.DataFrame) -> int:
    vals = set(_map_required_columns(df).values())
    return sum(
        1
        for c in (
            "Date Of Allocation",
            "Operator/Driver ID",
            "Type Of Plan",
            "Vehicle Number",
        )
        if c in vals
    )


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

    keep = [c for c in required if c in mapped_vals]
    if "Driver Plan" in mapped_vals:
        keep.append("Driver Plan")
    out = df.rename(columns=mapping)[keep].copy()

    out["Vehicle Number"] = out["Vehicle Number"].map(_norm_vehicle_key)
    out["Operator/Driver ID"] = out["Operator/Driver ID"].map(_norm_id_key)
    out["Type Of Plan"] = out["Type Of Plan"].map(_normalize_text)
    if "Driver Plan" in out.columns:
        out["Driver Plan"] = out["Driver Plan"].map(_normalize_text)
        blank_plan = out["Type Of Plan"].eq("")
        out.loc[blank_plan, "Type Of Plan"] = out.loc[blank_plan, "Driver Plan"]
        out = out.drop(columns=["Driver Plan"])

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
    out.attrs["column_map"] = {v: k for k, v in mapping.items()}
    return out


def load_pan_india_allocation(
    folder: Path | None = None, *, force_sync: bool = False
) -> tuple[pd.DataFrame, dict]:
    folder = folder or pan_india_dir()
    sync_info = sync_pan_india_sheet(force=force_sync)
    files = list_pan_india_files(folder)
    if not files and not sync_info.get("ok"):
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


def _asof_plan_lookup(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    left_key: str,
    right_key: str,
) -> pd.Series:
    """For each left row, plan from max Date Of Allocation <= _asof on matching key."""
    if left.empty or right.empty:
        return pd.Series(dtype=str)

    joined = left.merge(
        right.rename(columns={right_key: left_key}),
        on=left_key,
        how="inner",
    )
    joined = joined[joined["Date Of Allocation"] <= joined["_asof"]].copy()
    if joined.empty:
        return pd.Series(dtype=str)

    joined = joined.sort_values(["_row", "Date Of Allocation"])
    best = joined.groupby("_row", as_index=False).tail(1)
    return best.set_index("_row")["Type Of Plan"].fillna("").astype(str)


def attach_type_of_plan(
    fleet: pd.DataFrame,
    as_of_date: str | pd.Timestamp | None = None,
    pan_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Add Type Of Plan using ONLY Vehicle + Operator/Driver ID + closest past date.

    Never fall back to vehicle-only or id-only (that picks another partner's plan).
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

    if "_key" not in pan_df.columns:
        pan_df = pan_df.copy()
        veh = pan_df.get("Vehicle Number", pd.Series("", index=pan_df.index))
        pid = pan_df.get("Operator/Driver ID", pd.Series("", index=pan_df.index))
        pan_df["_key"] = [_match_key(v, p) for v, p in zip(veh, pid)]

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

    veh_series = out.get("Vehicle Number", pd.Series("", index=out.index)).fillna("")
    pid_series = out.get("Partner ID", pd.Series("", index=out.index)).fillna("")

    left = pd.DataFrame(
        {
            "_row": out.index.astype(int),
            "_key": [_match_key(v, p) for v, p in zip(veh_series, pid_series)],
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

    plan_map = _asof_plan_lookup(
        left,
        right,
        left_key="_key",
        right_key="_key",
    )

    out["Type Of Plan"] = (
        plan_map.reindex(out.index).fillna("").astype(str).map(_normalize_text)
    )
    out["Type Of Plan"] = out["Type Of Plan"].replace(
        {"nan": "", "None": "", "NaT": ""}
    )
    meta["matched"] = int((out["Type Of Plan"].astype(str).str.strip() != "").sum())

    sample_blank: list[str] = []
    blank_n = int(len(out) - meta["matched"])
    if blank_n:
        blanks = out.loc[
            out["Type Of Plan"].astype(str).str.strip().eq(""),
            ["Vehicle Number", "Partner ID"],
        ].head(5)
        for _, r in blanks.iterrows():
            sample_blank.append(
                f"{_norm_vehicle_key(r.get('Vehicle Number', ''))}|"
                f"{_norm_id_key(r.get('Partner ID', ''))}"
            )
        meta["blank_samples"] = sample_blank

    meta["message"] = (
        f"Pan India rows={meta['pan_rows']} · "
        f"Type Of Plan filled={meta['matched']}/{len(out)} "
        f"(strict Vehicle+ID only)"
    )
    if sample_blank:
        meta["message"] += f" · blank e.g. {', '.join(sample_blank[:3])}"
    return out, meta
