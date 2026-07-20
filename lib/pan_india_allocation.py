"""Pan India Allocation — Type Of Plan by Vehicle + Operator/Driver ID.

Required columns from live Google Sheet:
  - Date Of Allocation   (DD/MM/YYYY — India format)
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
from datetime import datetime
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


def _trim(value) -> str:
    """Aggressive trim for plan text (spaces, NBSP, zero-width)."""
    if pd.isna(value):
        return ""
    text = str(value)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]", "", text)
    text = text.strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _norm_vehicle_key(value) -> str:
    """Same vehicle trim as allocation / Uber / OLA / GPS / Rapido."""
    return _norm_vehicle(value)


def _norm_id_key(value) -> str:
    """Same Partner / Operator ID trim as allocation fleet table."""
    return _norm_partner_id(value)


def _parse_allocation_date(value) -> pd.Timestamp:
    """Date Of Allocation is DD/MM/YYYY (India). Never treat as MM/DD/YYYY."""
    if pd.isna(value):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value.normalize() if pd.notna(value) else pd.NaT
    text = _trim(value)
    if not text or text.lower() in {"nan", "none", "null", "nat", "-"}:
        return pd.NaT

    # Explicit DD/MM/YYYY (and common separators) — first priority
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return pd.Timestamp(datetime.strptime(text, fmt)).normalize()
        except ValueError:
            pass

    # Already ISO from Excel/Sheets export: 2024-06-12
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return pd.Timestamp(datetime.strptime(text, fmt)).normalize()
        except ValueError:
            pass

    # Excel serial number as string
    if re.fullmatch(r"\d+(\.\d+)?", text):
        try:
            serial = float(text)
            if 20000 < serial < 80000:
                return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=serial)).normalize()
        except (ValueError, OverflowError):
            pass

    return pd.NaT


def _parse_allocation_date_series(raw: pd.Series) -> pd.Series:
    return raw.map(_parse_allocation_date)


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

    raw_dates = out["Date Of Allocation"].copy()
    # Sheet uses DD/MM/YYYY — never parse as US MM/DD/YYYY
    out["Date Of Allocation"] = _parse_allocation_date_series(raw_dates)
    # Unparseable dates kept as old sentinel so Vehicle+ID can still match
    still_nat = out["Date Of Allocation"].isna()
    if still_nat.any():
        out.loc[still_nat, "Date Of Allocation"] = pd.Timestamp("1900-01-01")

    out = out[out["Vehicle Number"] != ""].copy()
    out = out[out["Operator/Driver ID"] != ""].copy()
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


def _phone_tail(partner_id: str) -> str:
    """Last 10 digits of ID (LETZHYDIP9550473489 → 9550473489)."""
    digits = re.sub(r"\D", "", _norm_id_key(partner_id))
    return digits[-10:] if len(digits) >= 10 else ""


def _latest_plan_by_key(right: pd.DataFrame, key_col: str = "_key") -> pd.Series:
    """Latest Type Of Plan per key (any date)."""
    if right.empty:
        return pd.Series(dtype=str)
    work = right.sort_values([key_col, "Date Of Allocation"])
    best = work.groupby(key_col, as_index=False).tail(1)
    return best.set_index(key_col)["Type Of Plan"].fillna("").astype(str)


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
    Add Type Of Plan using Vehicle + Operator/Driver ID.

    Order (never vehicle-only / other-partner guess):
      1) same Vehicle+ID, closest Date Of Allocation <= row Date
      2) same Vehicle+ID, latest plan any date (fixes future/bad sheet dates)
      3) same Vehicle + phone-tail of ID (LETZ…9550473489 ↔ 9550473489)
    """
    out = fleet.copy()
    if "Type Of Plan" not in out.columns:
        out["Type Of Plan"] = ""

    meta = {
        "pan_rows": 0,
        "matched": 0,
        "matched_asof": 0,
        "matched_latest": 0,
        "matched_phone": 0,
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

    pan_df = pan_df.copy()
    if "Vehicle Number" not in pan_df.columns and "_veh_key" in pan_df.columns:
        pan_df["Vehicle Number"] = pan_df["_veh_key"]
    if "Operator/Driver ID" not in pan_df.columns and "_id_key" in pan_df.columns:
        pan_df["Operator/Driver ID"] = pan_df["_id_key"]

    veh_p = pan_df.get("Vehicle Number", pd.Series("", index=pan_df.index)).map(
        _norm_vehicle_key
    )
    id_p = pan_df.get("Operator/Driver ID", pd.Series("", index=pan_df.index)).map(
        _norm_id_key
    )
    pan_df["_key"] = [_match_key(v, p) for v, p in zip(veh_p, id_p)]
    pan_df["_veh_key"] = veh_p
    pan_df["_phone"] = [_phone_tail(p) for p in id_p]
    pan_df["_veh_phone"] = [
        f"{v}|{ph}" if v and ph else ""
        for v, ph in zip(pan_df["_veh_key"], pan_df["_phone"])
    ]

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
    veh_l = [_norm_vehicle_key(v) for v in veh_series]
    id_l = [_norm_id_key(p) for p in pid_series]
    phone_l = [_phone_tail(p) for p in pid_series]

    left = pd.DataFrame(
        {
            "_row": out.index.astype(int),
            "_key": [f"{v}|{i}" if v and i else "|" for v, i in zip(veh_l, id_l)],
            "_veh_phone": [
                f"{v}|{ph}" if v and ph else "" for v, ph in zip(veh_l, phone_l)
            ],
            "_asof": asof,
        },
        index=out.index,
    )
    left = left[left["_asof"].notna()].copy()

    right = pan_df[
        ["_key", "_veh_phone", "Date Of Allocation", "Type Of Plan"]
    ].copy()
    right["Date Of Allocation"] = pd.to_datetime(
        right["Date Of Allocation"], errors="coerce"
    ).dt.normalize()
    right = right[
        right["Date Of Allocation"].notna()
        & right["Type Of Plan"].fillna("").astype(str).str.strip().ne("")
    ].copy()

    if left.empty or right.empty:
        meta["message"] = "No valid Vehicle+ID keys to match Type Of Plan"
        return out, meta

    plan_map = pd.Series("", index=out.index, dtype=object)
    how = pd.Series("", index=out.index, dtype=object)

    # 1) Exact Vehicle+ID, as-of date
    primary = _asof_plan_lookup(
        left[left["_key"].ne("|")],
        right[["_key", "Date Of Allocation", "Type Of Plan"]],
        left_key="_key",
        right_key="_key",
    )
    for row_i, plan in primary.items():
        if str(plan).strip():
            plan_map.loc[row_i] = str(plan).strip()
            how.loc[row_i] = "asof"
    meta["matched_asof"] = int((how == "asof").sum())

    # 2) Exact Vehicle+ID, latest any date (same partner only)
    still = plan_map.astype(str).str.strip().eq("")
    if still.any():
        latest = _latest_plan_by_key(
            right[["_key", "Date Of Allocation", "Type Of Plan"]], "_key"
        )
        for idx in out.index[still]:
            if idx not in left.index:
                continue
            key = str(left.at[idx, "_key"] or "")
            if key and key != "|" and key in latest.index:
                plan = str(latest.loc[key]).strip()
                if plan:
                    plan_map.loc[idx] = plan
                    how.loc[idx] = "latest"
        meta["matched_latest"] = int((how == "latest").sum())

    # 3) Vehicle + phone-tail (handles LETZ…ID vs plain phone in sheet)
    still = plan_map.astype(str).str.strip().eq("")
    if still.any():
        blank_idx = out.index[still]
        left_ph = left.loc[left.index.intersection(blank_idx)]
        left_ph = left_ph[left_ph["_veh_phone"].ne("")]
        if not left_ph.empty:
            right_ph = right[right["_veh_phone"].ne("")].copy()
            phone_asof = _asof_plan_lookup(
                left_ph,
                right_ph[["_veh_phone", "Date Of Allocation", "Type Of Plan"]],
                left_key="_veh_phone",
                right_key="_veh_phone",
            )
            for row_i, plan in phone_asof.items():
                if str(plan).strip() and not str(plan_map.loc[row_i]).strip():
                    plan_map.loc[row_i] = str(plan).strip()
                    how.loc[row_i] = "phone"
            still = plan_map.astype(str).str.strip().eq("")
            blank_idx = out.index[still]
            left_ph2 = left.loc[left.index.intersection(blank_idx)]
            left_ph2 = left_ph2[left_ph2["_veh_phone"].ne("")]
            if not left_ph2.empty:
                latest_ph = _latest_plan_by_key(
                    right_ph[["_veh_phone", "Date Of Allocation", "Type Of Plan"]],
                    "_veh_phone",
                )
                for idx in left_ph2.index:
                    key = str(left_ph2.at[idx, "_veh_phone"] or "")
                    if key and key in latest_ph.index:
                        plan = str(latest_ph.loc[key]).strip()
                        if plan:
                            plan_map.loc[idx] = plan
                            how.loc[idx] = "phone"
        meta["matched_phone"] = int((how == "phone").sum())

    out["Type Of Plan"] = (
        plan_map.reindex(out.index).fillna("").astype(str).map(_normalize_text)
    )
    out["Type Of Plan"] = out["Type Of Plan"].replace(
        {"nan": "", "None": "", "NaT": ""}
    )
    meta["matched"] = int((out["Type Of Plan"].astype(str).str.strip() != "").sum())

    pan_keys = set(right["_key"].tolist())
    sample_blank: list[str] = []
    blank_n = int(len(out) - meta["matched"])
    missing_in_sheet = 0
    if blank_n:
        blanks = out.loc[
            out["Type Of Plan"].astype(str).str.strip().eq(""),
            ["Vehicle Number", "Partner ID"],
        ].head(8)
        for _, r in blanks.iterrows():
            key = _match_key(r.get("Vehicle Number", ""), r.get("Partner ID", ""))
            sample_blank.append(key)
            if key not in pan_keys:
                missing_in_sheet += 1
        meta["blank_samples"] = sample_blank
        meta["blank_not_in_sheet"] = missing_in_sheet

    meta["message"] = (
        f"Pan India rows={meta['pan_rows']} · "
        f"Type Of Plan filled={meta['matched']}/{len(out)} "
        f"(asof={meta['matched_asof']}, latest={meta['matched_latest']}, "
        f"phone={meta['matched_phone']})"
    )
    if sample_blank:
        meta["message"] += f" · blank e.g. {', '.join(sample_blank[:3])}"
        if missing_in_sheet:
            meta["message"] += (
                f" · {missing_in_sheet} blank keys not found in synced Pan India"
            )
    return out, meta
