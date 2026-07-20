"""Partner onboarding details + active/inactive partner view."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from lib.paths import data_root

SUPPORTED_SUFFIXES = {".xlsx", ".xls", ".csv"}


def partner_details_dir(base: Path | None = None) -> Path:
    if base is not None:
        return Path(base)
    return data_root() / "Allocation & Drop off form"


def list_partner_detail_files(folder: Path | None = None) -> list[Path]:
    folder = folder or partner_details_dir()
    if not folder.exists():
        return []
    files = [
        p
        for p in folder.iterdir()
        if p.is_file()
        and not p.name.startswith("~$")
        and p.suffix.lower() in SUPPORTED_SUFFIXES
    ]
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
    return "" if text.lower() in {"nan", "none", "null", "nat", "-"} else text


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
    out = out[out["Partner ID"] != ""].copy()
    out["Driver DOB"] = pd.to_datetime(
        out["Driver Date of Birth"], errors="coerce", dayfirst=True
    ).dt.normalize()
    out["Onboarding Type"] = out["Onboarding Type"].replace("", "Unknown")
    out["City"] = out["City"].replace("", "Unknown")
    out["Driver Name"] = out["Driver Name"].replace("", "Unknown")
    out = out.drop_duplicates(
        subset=["Partner ID", "Driver Name", "Driver Date of Birth"], keep="last"
    ).reset_index(drop=True)
    return out


def load_partner_details(folder: Path | None = None) -> tuple[pd.DataFrame, dict]:
    folder = folder or partner_details_dir()
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
            "message": (
                "No exported onboarding file found. Add an .xlsx/.xls/.csv file in "
                f"`{folder}`."
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
        "message": "Loaded onboarding details",
    }


def _age_bucket(age: float | int | None) -> str:
    if age is None or pd.isna(age):
        return "Unknown"
    age = int(age)
    if age < 25:
        return "< 25"
    if age <= 34:
        return "25-34"
    if age <= 44:
        return "35-44"
    if age <= 54:
        return "45-54"
    return "55+"


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
    }
