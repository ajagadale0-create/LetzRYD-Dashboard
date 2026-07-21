"""Partner onboarding details + active/inactive partner view."""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from lib.paths import data_root
from lib.allocation import _norm_partner_id

SUPPORTED_SUFFIXES = {".xlsx", ".xls", ".csv"}
PARTNER_EXPORT_NAME = "Partner Details.xlsx"
AGE_BUCKETS = ["< 25", "25-34", "35-44", "45-54", "55+", "Unknown"]


def partner_details_dir(base: Path | None = None) -> Path:
    if base is not None:
        return Path(base)
    return data_root() / "Allocation & Drop off form"


def _partner_sheet_id() -> str:
    env_id = os.environ.get("PARTNER_SHEET_ID", "").strip()
    if env_id:
        return env_id
    try:
        import streamlit as st

        return str(st.secrets.get("drive", {}).get("partner_sheet_id", "")).strip()
    except Exception:
        return ""


def _partner_id_file(folder: Path) -> Path:
    return folder / ".partner_sheet_id"


def sync_partner_sheet(*, sheet_id: str | None = None) -> dict:
    """Auto-export Partner Details Google Sheet via service account (no manual Excel)."""
    _ = sheet_id
    try:
        from lib.drive_sync import sync_named_sheet_from_drive

        return sync_named_sheet_from_drive(
            name_hint="partner details",
            dest_name=PARTNER_EXPORT_NAME,
            force=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Partner auto-sync failed: {exc}",
            "bytes": 0,
            "path": "",
        }


def list_partner_detail_files(folder: Path | None = None) -> list[Path]:
    from lib.paths import code_root

    folder = folder or partner_details_dir()
    search_dirs = [folder]
    cache_dir = code_root() / ".data_cache" / "Allocation & Drop off form"
    try:
        if cache_dir.exists() and cache_dir.resolve() != Path(folder).resolve():
            search_dirs.append(cache_dir)
    except OSError:
        if cache_dir.exists():
            search_dirs.append(cache_dir)

    files: list[Path] = []
    for d in search_dirs:
        if not d.exists():
            continue
        for p in d.iterdir():
            if not p.is_file() or p.name.startswith("~$"):
                continue
            if p.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            name = p.name.lower()
            if "partner" in name and "pan india" not in name:
                files.append(p)
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _existing_gsheet_names(folder: Path) -> list[str]:
    if not folder.exists():
        return []
    return sorted(
        [p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".gsheet"]
    )


def _normalize_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]", "", text)
    return "" if text.lower() in {"nan", "none", "null", "nat", "-"} else text


def _parse_driver_dob(value) -> pd.Timestamp:
    """
    Partner sheet DOB is DD/MMM/YY (e.g. 05/Mar/95) or DD/MM/YYYY.
    Also accepts ISO / Excel serial from Sheets→XLSX export.
    """
    if pd.isna(value):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        ts = value.normalize() if pd.notna(value) else pd.NaT
    elif hasattr(value, "year"):
        try:
            ts = pd.Timestamp(value).normalize()
        except Exception:
            ts = pd.NaT
    else:
        text = _normalize_text(value)
        if not text:
            return pd.NaT
        ts = pd.NaT
        for fmt in (
            "%d/%b/%y",
            "%d/%b/%Y",
            "%d-%b-%y",
            "%d-%b-%Y",
            "%d %b %y",
            "%d %b %Y",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%d/%m/%y",
            "%d-%m-%y",
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
        ):
            try:
                ts = pd.Timestamp(datetime.strptime(text, fmt)).normalize()
                break
            except ValueError:
                continue
        if pd.isna(ts) and re.fullmatch(r"\d+(\.\d+)?", text):
            try:
                serial = float(text)
                if 20000 < serial < 80000:
                    ts = (
                        pd.Timestamp("1899-12-30") + pd.Timedelta(days=serial)
                    ).normalize()
            except (ValueError, OverflowError):
                pass
        if pd.isna(ts):
            # dayfirst last — India dates; never prefer US MM/DD
            parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
            ts = pd.Timestamp(parsed).normalize() if pd.notna(parsed) else pd.NaT

    if pd.isna(ts):
        return pd.NaT
    # Placeholder / junk years (e.g. 1900) → treat as missing
    if ts.year < 1940 or ts.year > pd.Timestamp.today().year:
        return pd.NaT
    return ts


ONBOARDING_TYPE_CANON = {
    "operator": "Operator",
    "individual": "Individual",
    "owner": "Owner",
    "driver": "Driver",
}


def _normalize_onboarding_type(value) -> str:
    text = _normalize_text(value)
    if not text:
        return "Unknown"
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\s\xa0\u200b\u200c\u200d\ufeff]+", " ", text).strip()
    text = re.sub(r"[^\w\s/&-]", "", text).strip()
    key = re.sub(r"\s+", " ", text).casefold().strip()
    if key in ONBOARDING_TYPE_CANON:
        return ONBOARDING_TYPE_CANON[key]
    # e.g. "Operator Operator" or "operator operator"
    parts = [p for p in key.split() if p]
    if parts and len(set(parts)) == 1:
        return ONBOARDING_TYPE_CANON.get(parts[0], parts[0].title())
    for alias, canon in ONBOARDING_TYPE_CANON.items():
        if key == alias or key.startswith(alias + " "):
            return canon
    return text.title()


def onboarding_type_options(values) -> list[str]:
    """Unique onboarding types for dropdowns (Operator/operator → one Operator)."""
    canon: dict[str, str] = {}
    for raw in values:
        label = _normalize_onboarding_type(raw)
        if label == "Unknown":
            continue
        key = label.casefold()
        canon.setdefault(key, label)
    return sorted(canon.values(), key=lambda s: (s != "Operator", s != "Individual", s))


def _read_partner_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path, dtype=str)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
    else:
        raise ValueError(f"Unsupported onboarding file: {path.name}")

    rename = {}
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    wanted = {
        "onboarding type": "Onboarding Type",
        "city": "City",
        "id s creations": "Partner ID",
        "id's creations": "Partner ID",
        "ids creations": "Partner ID",
        "id creations": "Partner ID",
        "id creation": "Partner ID",
        "driver name": "Driver Name",
        "driver date of birth (dd/mmm/yy)": "Driver Date of Birth",
        "driver date of birth": "Driver Date of Birth",
        "date of birth": "Driver Date of Birth",
        "dob": "Driver Date of Birth",
        "duplicate check": "Duplicate Check",
    }
    for key, canon in wanted.items():
        if key in lower_map:
            rename[lower_map[key]] = canon

    # Fuzzy DOB header if exact title didn't match
    if "Driver Date of Birth" not in rename.values():
        for low, raw in lower_map.items():
            if "birth" in low or re.search(r"\bdob\b", low):
                rename[raw] = "Driver Date of Birth"
                break

    df = df.rename(columns=rename)

    required = [
        "Onboarding Type",
        "City",
        "Partner ID",
        "Duplicate Check",
        "Driver Name",
        "Driver Date of Birth",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"{path.name} missing columns: {missing}")

    out = df[required].copy()
    for col in required:
        out[col] = out[col].map(_normalize_text)

    out = out[out["Duplicate Check"].str.casefold() == "unique"].copy()
    out["Partner ID"] = out["Partner ID"].map(_normalize_text).map(_norm_partner_id)
    out = out[out["Partner ID"] != ""].copy()
    out["Driver DOB"] = out["Driver Date of Birth"].map(_parse_driver_dob)
    out["Onboarding Type"] = out["Onboarding Type"].map(_normalize_onboarding_type)
    out["City"] = out["City"].replace("", "Unknown")
    out["Driver Name"] = out["Driver Name"].replace("", "Unknown")
    out = out.drop_duplicates(
        subset=["Partner ID", "Driver Name", "Driver Date of Birth"], keep="last"
    ).reset_index(drop=True)
    return out


def load_partner_details(folder: Path | None = None) -> tuple[pd.DataFrame, dict]:
    folder = folder or partner_details_dir()
    sync_info = sync_partner_sheet()
    files = list_partner_detail_files(folder)
    gsheet_names = _existing_gsheet_names(folder)
    loaded: list[str] = []
    errors: list[str] = []

    if not files:
        return pd.DataFrame(), {
            "rows": 0,
            "loaded_file": "",
            "files": [],
            "gsheet_files": gsheet_names,
            "errors": [],
            "sync": sync_info,
            "message": (
                "Partner Details Google Sheet auto-sync failed. "
                "Share the sheet with the service account as Viewer "
                "(same as AI Dashboard folder)."
            ),
        }

    path = files[0]
    try:
        df = _read_partner_file(path)
        loaded.append(path.name)
    except Exception as exc:
        errors.append(f"{path.name}: {exc}")
        return pd.DataFrame(), {
            "rows": 0,
            "loaded_file": path.name,
            "files": [path.name],
            "gsheet_files": gsheet_names,
            "errors": errors,
            "message": "Could not read onboarding file.",
        }

    return df, {
        "rows": len(df),
        "loaded_file": path.name,
        "files": loaded,
        "gsheet_files": gsheet_names,
        "errors": errors,
        "sync": sync_info,
        "message": "Loaded onboarding details",
    }


def _age_bucket(age: float | int | None) -> str:
    if age is None or pd.isna(age):
        return "Unknown"
    age = int(age)
    if age < 0 or age > 100:
        return "Unknown"
    if age < 25:
        return AGE_BUCKETS[0]
    if age <= 34:
        return AGE_BUCKETS[1]
    if age <= 44:
        return AGE_BUCKETS[2]
    if age <= 54:
        return AGE_BUCKETS[3]
    return AGE_BUCKETS[4]


def build_ageing_basket_summary(view: pd.DataFrame) -> pd.DataFrame:
    """Pivot: one row per age bucket with Active / Inactive / Total / Active %."""
    empty = pd.DataFrame(columns=["Ageing", "Active", "Inactive", "Total", "Active %"])
    if view is None or view.empty:
        return empty

    pivot = (
        view.groupby(["Ageing", "Partner Status"], as_index=False)
        .agg(Partners=("Partner ID", "nunique"))
        .pivot(index="Ageing", columns="Partner Status", values="Partners")
        .fillna(0)
    )
    for col in ("Active", "Inactive"):
        if col not in pivot.columns:
            pivot[col] = 0

    out = pivot[["Active", "Inactive"]].astype(int).reset_index()
    # Keep all buckets, even if count is zero
    base = pd.DataFrame({"Ageing": AGE_BUCKETS})
    out = base.merge(out, on="Ageing", how="left").fillna(0)
    out["Active"] = out["Active"].astype(int)
    out["Inactive"] = out["Inactive"].astype(int)
    out["Total"] = out["Active"] + out["Inactive"]
    out["Active %"] = out.apply(
        lambda r: round(100 * r["Active"] / r["Total"], 1) if r["Total"] else 0.0,
        axis=1,
    )

    total_row = pd.DataFrame(
        [
            {
                "Ageing": "Total",
                "Active": int(out["Active"].sum()),
                "Inactive": int(out["Inactive"].sum()),
                "Total": int(out["Total"].sum()),
                "Active %": round(
                    100 * out["Active"].sum() / out["Total"].sum(), 1
                )
                if out["Total"].sum()
                else 0.0,
            }
        ]
    )
    return pd.concat([out, total_row], ignore_index=True)


def _latest_allocation_day(alloc_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp]:
    if alloc_df is None or alloc_df.empty or "Date" not in alloc_df.columns:
        return pd.DataFrame(), pd.NaT
    active_ts = pd.to_datetime(alloc_df["Date"], errors="coerce").max()
    if pd.isna(active_ts):
        return pd.DataFrame(), pd.NaT
    day = alloc_df[pd.to_datetime(alloc_df["Date"], errors="coerce") == active_ts].copy()
    return day, pd.Timestamp(active_ts).normalize()


def build_operator_vehicle_summary(
    alloc_df: pd.DataFrame,
    partner_view: pd.DataFrame,
) -> pd.DataFrame:
    """Operators grouped by how many vehicles they have on latest allocation date."""
    operators = partner_view[
        partner_view["Onboarding Type"].fillna("").astype(str).str.strip() == "Operator"
    ].copy()
    if operators.empty:
        return pd.DataFrame(columns=["Vehicles", "Operators", "Active", "Inactive"])

    day, _ = _latest_allocation_day(alloc_df)
    veh_counts = pd.DataFrame(columns=["Partner ID", "Vehicle Count"])
    if not day.empty and "partner IDs" in day.columns:
        alloc_ops = day.copy()
        alloc_ops["Partner ID"] = alloc_ops["partner IDs"].map(_norm_partner_id)
        alloc_ops = alloc_ops[
            (alloc_ops["Partner ID"] != "") & (alloc_ops["Partner ID"].str.upper() != "RFD")
        ]
        if not alloc_ops.empty:
            veh_counts = (
                alloc_ops.groupby("Partner ID", as_index=False)
                .agg(**{"Vehicle Count": ("Vehicle Number", "nunique")})
            )

    ops = operators.drop_duplicates("Partner ID", keep="last")
    merged = ops.merge(veh_counts, on="Partner ID", how="left")
    merged["Vehicle Count"] = merged["Vehicle Count"].fillna(0).astype(int)

    summary = (
        merged.groupby("Vehicle Count", as_index=False)
        .agg(
            Operators=("Partner ID", "nunique"),
            Active=("Partner Status", lambda s: int((s == "Active").sum())),
            Inactive=("Partner Status", lambda s: int((s == "Inactive").sum())),
        )
        .sort_values("Vehicle Count")
        .reset_index(drop=True)
    )
    summary = summary.rename(columns={"Vehicle Count": "Vehicles"})
    total = pd.DataFrame(
        [
            {
                "Vehicles": "Total",
                "Operators": int(summary["Operators"].sum()),
                "Active": int(summary["Active"].sum()),
                "Inactive": int(summary["Inactive"].sum()),
            }
        ]
    )
    return pd.concat([summary, total], ignore_index=True)


def build_operator_vehicle_table(
    alloc_df: pd.DataFrame,
    partner_view: pd.DataFrame,
) -> pd.DataFrame:
    """One row per Operator partner ID with vehicle count on latest allocation date."""
    operators = partner_view[
        partner_view["Onboarding Type"].fillna("").astype(str).str.strip() == "Operator"
    ].copy()
    if operators.empty:
        return pd.DataFrame(
            columns=[
                "Partner ID",
                "Driver Name",
                "City",
                "Vehicle Count",
                "Partner Status",
                "Ageing",
                "Driver Age",
            ]
        )

    day, _ = _latest_allocation_day(alloc_df)
    veh_counts = pd.DataFrame(columns=["Partner ID", "Vehicle Count"])
    if not day.empty and "partner IDs" in day.columns:
        alloc_ops = day.copy()
        alloc_ops["Partner ID"] = alloc_ops["partner IDs"].map(_norm_partner_id)
        alloc_ops = alloc_ops[
            (alloc_ops["Partner ID"] != "") & (alloc_ops["Partner ID"].str.upper() != "RFD")
        ]
        if not alloc_ops.empty:
            veh_counts = (
                alloc_ops.groupby("Partner ID", as_index=False)
                .agg(**{"Vehicle Count": ("Vehicle Number", "nunique")})
            )

    ops = operators.drop_duplicates("Partner ID", keep="last")
    out = ops.merge(veh_counts, on="Partner ID", how="left")
    out["Vehicle Count"] = out["Vehicle Count"].fillna(0).astype(int)
    out = out[
        [
            "Partner ID",
            "Driver Name",
            "City",
            "Vehicle Count",
            "Partner Status",
            "Ageing",
            "Driver Age",
        ]
    ].sort_values(
        ["Vehicle Count", "Partner Status", "Driver Name"],
        ascending=[False, True, True],
    )
    return out.reset_index(drop=True)


def build_partner_status_table(
    partner_df: pd.DataFrame,
    alloc_df: pd.DataFrame,
    active_date: str | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict]:
    if partner_df is None or partner_df.empty:
        return pd.DataFrame(), {
            "active_date": "",
            "active_ids": 0,
            "partners": 0,
            "active_partners": 0,
            "inactive_partners": 0,
        }

    alloc = alloc_df.copy() if alloc_df is not None else pd.DataFrame()
    if alloc.empty or "Date" not in alloc.columns:
        active_ids: set[str] = set()
        active_ts = pd.NaT
    else:
        if active_date is None:
            active_ts = pd.to_datetime(alloc["Date"], errors="coerce").max()
        else:
            active_ts = pd.Timestamp(active_date).normalize()
        day = alloc[pd.to_datetime(alloc["Date"], errors="coerce") == active_ts].copy()
        active_ids = {
            str(v).strip()
            for v in day.get("partner IDs", pd.Series(dtype=str)).fillna("").tolist()
            if str(v).strip() and str(v).strip().upper() != "RFD"
        }

    out = partner_df.copy()
    asof = active_ts.normalize() if pd.notna(active_ts) else pd.Timestamp.today().normalize()
    age_years = ((asof - out["Driver DOB"]).dt.days / 365.25).floordiv(1)
    out["Driver Age"] = age_years.where(out["Driver DOB"].notna())
    out["Driver Age"] = out["Driver Age"].astype("Int64")
    out["Ageing"] = out["Driver Age"].map(_age_bucket)
    out["Partner Status"] = out["Partner ID"].map(
        lambda pid: "Active" if str(pid).strip() in active_ids else "Inactive"
    )
    out["Active Date"] = (
        pd.Timestamp(active_ts).strftime("%Y-%m-%d") if pd.notna(active_ts) else ""
    )
    out["Driver DOB Display"] = out["Driver DOB"].dt.strftime("%d-%b-%Y")
    out["Driver DOB Display"] = out["Driver DOB Display"].fillna("")
    display_cols = [
        "Onboarding Type",
        "City",
        "Partner ID",
        "Partner Status",
        "Driver Name",
        "Driver DOB Display",
        "Driver Age",
        "Ageing",
    ]
    out = out[display_cols].rename(columns={"Driver DOB Display": "Driver Date of Birth"})
    out["Onboarding Type"] = out["Onboarding Type"].map(_normalize_onboarding_type)
    out["Driver Age"] = out["Driver Age"].astype("Int64")
    out = out.sort_values(
        ["Partner Status", "Onboarding Type", "City", "Driver Name", "Partner ID"],
        ascending=[True, True, True, True, True],
    ).reset_index(drop=True)
    return out, {
        "active_date": pd.Timestamp(active_ts).strftime("%Y-%m-%d") if pd.notna(active_ts) else "",
        "active_ids": len(active_ids),
        "partners": out["Partner ID"].nunique(),
        "active_partners": int(out.loc[out["Partner Status"] == "Active", "Partner ID"].nunique()),
        "inactive_partners": int(
            out.loc[out["Partner Status"] == "Inactive", "Partner ID"].nunique()
        ),
        "dob_unknown": int((out["Ageing"] == "Unknown").sum()),
        "dob_parsed": int((out["Ageing"] != "Unknown").sum()),
    }
