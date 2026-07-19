"""Vehicle allocation status: historical Excel + current-day exports (from GSheet)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ALLOC_COLS = [
    "City",
    "Vehicle Number",
    "Date",
    "Allocation Date",
    "Drop Off Date",
    "partner Name",
    "partner IDs",
    "DM Name",
    "Type",
    "Final Status",
]


def allocation_dir(base: Path | None = None) -> Path:
    if base is not None:
        return Path(base)
    from lib.paths import data_root

    return data_root() / "Vehicle Allocation Status"


def _norm_vehicle(value) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", "", str(value).strip().upper())


def _norm_partner_id(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "-"}:
        return ""
    return text


def _read_alloc_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path, dtype=str)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
    else:
        raise ValueError(f"Unsupported allocation file: {path}")

    # Normalize header spacing / case variants
    rename = {}
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    wanted = {
        "city": "City",
        "vehicle number": "Vehicle Number",
        "date": "Date",
        "allocation date": "Allocation Date",
        "drop off date": "Drop Off Date",
        "drop-off date": "Drop Off Date",
        "partner name": "partner Name",
        "partner ids": "partner IDs",
        "partner id": "partner IDs",
        "dm name": "DM Name",
        "type": "Type",
        "final status": "Final Status",
    }
    for key, canon in wanted.items():
        if key in lower_map:
            rename[lower_map[key]] = canon
    df = df.rename(columns=rename)

    missing = [c for c in ("Vehicle Number", "Date") if c not in df.columns]
    if missing:
        raise KeyError(f"{path.name} missing columns: {missing}")

    keep = [c for c in ALLOC_COLS if c in df.columns]
    out = df[keep].copy()
    out["Vehicle Number"] = out["Vehicle Number"].map(_norm_vehicle)
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    for col in ("Allocation Date", "Drop Off Date"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
        else:
            out[col] = pd.NaT
    for col in ("City", "partner Name", "partner IDs", "DM Name", "Type", "Final Status"):
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)
        out[col] = out[col].replace({"nan": "", "None": "", "NaT": ""})
        out[col] = out[col].map(lambda x: str(x).strip())
    out["partner IDs"] = out["partner IDs"].map(_norm_partner_id)
    out["source_file"] = path.name
    out = out[out["Vehicle Number"] != ""].copy()
    out = out[out["Date"].notna()].copy()
    return out


OLD_ALLOC_NAME_PREFIXES = ("Daily Vehicle Status",)
CURRENT_ALLOC_NAMES = {
    "Current Vehicle Allocation Sheet.xlsx",
    "Current Vehicle Allocation Sheet_latest.xlsx",
}


def list_allocation_files(folder: Path | None = None) -> list[Path]:
    """Only the two Excels: historical (old) + current (synced from GSheet)."""
    folder = folder or allocation_dir()
    if not folder.exists():
        return []

    old_files = sorted(
        [
            p
            for p in folder.glob("*.xlsx")
            if p.name.startswith(OLD_ALLOC_NAME_PREFIXES)
            and not p.name.startswith("~$")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Keep only the newest historical workbook (never grow a third file)
    chosen: list[Path] = []
    if old_files:
        chosen.append(old_files[0])

    for name in sorted(CURRENT_ALLOC_NAMES):
        cur = folder / name
        if cur.exists():
            chosen.append(cur)
            break

    return chosen


def _is_current_source(filename: str) -> bool:
    return "current" in filename.lower()


def load_allocation(folder: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Combine old Excel + current Excel with a dynamic cutover.

    Every run:
      1) Read old Excel and take its **latest Date**
      2) Keep all old-Excel rows through that date
      3) From GSheet/Current Excel take rows from **next day onward**

    Example: old max = 30 Jun → Current used from 1 Jul (incl. 15 Jul).
    If old Excel later grows to 10 Jul → Current starts from 11 Jul.
    """
    folder = folder or allocation_dir()
    old_df = pd.DataFrame(columns=ALLOC_COLS + ["source_file"])
    current_df = pd.DataFrame(columns=ALLOC_COLS + ["source_file"])
    loaded: list[str] = []
    errors: list[str] = []

    for path in list_allocation_files(folder):
        try:
            df = _read_alloc_file(path)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        loaded.append(path.name)
        if _is_current_source(path.name):
            current_df = df
        else:
            old_df = df

    old_max = old_df["Date"].max() if not old_df.empty else pd.NaT
    if pd.notna(old_max):
        cutover = pd.Timestamp(old_max).normalize() + pd.Timedelta(days=1)
        old_part = old_df[old_df["Date"] <= old_max].copy()
        current_part = (
            current_df[current_df["Date"] >= cutover].copy()
            if not current_df.empty
            else current_df
        )
    else:
        # No old file → use whatever is in Current
        cutover = pd.NaT
        old_part = old_df
        current_part = current_df

    frames = [f for f in (old_part, current_part) if f is not None and not f.empty]
    if not frames:
        empty = pd.DataFrame(columns=ALLOC_COLS + ["source_file"])
        return empty, {
            "files": loaded,
            "errors": errors,
            "rows": 0,
            "date_from": "",
            "date_to": "",
            "has_current": any(_is_current_source(f) for f in loaded),
            "old_file": next((f for f in loaded if not _is_current_source(f)), ""),
            "current_file": next((f for f in loaded if _is_current_source(f)), ""),
            "old_max_date": "",
            "cutover": "",
            "old_rows_kept": 0,
            "current_rows_kept": 0,
            "rule": "old Excel through its latest date; Current/GSheet from next day",
        }

    all_df = pd.concat(frames, ignore_index=True)
    all_df["_prio"] = all_df["source_file"].map(
        lambda n: 1 if _is_current_source(str(n)) else 0
    )
    all_df = (
        all_df.sort_values(["Vehicle Number", "Date", "_prio"])
        .drop_duplicates(["Vehicle Number", "Date"], keep="last")
        .drop(columns=["_prio"])
        .reset_index(drop=True)
    )

    dmin = all_df["Date"].min()
    dmax = all_df["Date"].max()
    meta = {
        "files": loaded,
        "errors": errors,
        "rows": len(all_df),
        "date_from": dmin.strftime("%Y-%m-%d") if pd.notna(dmin) else "",
        "date_to": dmax.strftime("%Y-%m-%d") if pd.notna(dmax) else "",
        "has_current": any(_is_current_source(f) for f in loaded),
        "old_file": next((f for f in loaded if not _is_current_source(f)), ""),
        "current_file": next((f for f in loaded if _is_current_source(f)), ""),
        "old_max_date": pd.Timestamp(old_max).strftime("%Y-%m-%d") if pd.notna(old_max) else "",
        "cutover": pd.Timestamp(cutover).strftime("%Y-%m-%d") if pd.notna(cutover) else "",
        "old_rows_kept": len(old_part),
        "current_rows_kept": len(current_part),
        "rule": "old Excel through its latest date; Current/GSheet from next day",
    }
    return all_df, meta


def allocation_lookup_for_date(
    alloc: pd.DataFrame,
    on_date: str | pd.Timestamp,
) -> pd.DataFrame:
    """
    Exact-day VLOOKUP only: Vehicle Number + Date == on_date.

    Partner ID / Partner Name / Type / DM Name come only from that day's Excel row.
    No carry-forward from other dates. If the vehicle has no row that day, partner fields stay blank.
    """
    target = pd.Timestamp(on_date).normalize()
    empty_cols = [
        "Vehicle Number",
        "Partner ID",
        "Partner Name",
        "Latest Allocation Date",
        "Drop Off Date",
        "Type",
        "DM Name",
        "Has Allocation Row",
    ]
    if alloc.empty:
        return pd.DataFrame(columns=empty_cols)

    day = alloc[alloc["Date"] == target].copy()
    if day.empty:
        return pd.DataFrame(columns=empty_cols)

    day = day.drop_duplicates("Vehicle Number", keep="last")
    out = day.rename(
        columns={
            "partner IDs": "Partner ID",
            "partner Name": "Partner Name",
        }
    ).copy()
    out["Has Allocation Row"] = True

    for col in ("Partner ID", "Partner Name", "DM Name", "Type"):
        if col not in out.columns:
            out[col] = ""
        out[col] = (
            out[col]
            .fillna("")
            .astype(str)
            .str.strip()
            .replace({"nan": "", "None": "", "NaT": ""})
        )
        out[col] = out[col].where(~out[col].str.lower().isin(["nan", "none", "nat"]), "")

    # Same-day only: if Name present but ID blank, fill ID from other rows that day with same name
    id_by_name: dict[str, str] = {}
    name_by_id: dict[str, str] = {}
    for _, r in day.iterrows():
        pid = _norm_partner_id(r.get("partner IDs", ""))
        pname = str(r.get("partner Name", "") or "").strip()
        if not pid or not pname or pname.lower() in {"nan", "none"}:
            continue
        id_by_name.setdefault(pname.upper(), pid)
        name_by_id.setdefault(pid.upper(), pname)

    blank_id = out["Partner ID"].eq("") & out["Partner Name"].ne("")
    out.loc[blank_id, "Partner ID"] = out.loc[blank_id, "Partner Name"].map(
        lambda n: id_by_name.get(str(n).upper(), "")
    )
    blank_name = out["Partner Name"].eq("") & out["Partner ID"].ne("")
    out.loc[blank_name, "Partner Name"] = out.loc[blank_name, "Partner ID"].map(
        lambda i: name_by_id.get(str(i).upper(), "")
    )

    # Allocation Date / Drop Off Date from that day's row only
    if "Allocation Date" in out.columns:
        out["Latest Allocation Date"] = out["Allocation Date"]
    else:
        out["Latest Allocation Date"] = pd.NaT

    if "Drop Off Date" not in out.columns:
        out["Drop Off Date"] = pd.NaT
    # Show drop-off only when Partner ID is blank that day
    row_drop = out["Drop Off Date"].copy()
    out["Drop Off Date"] = pd.NaT
    out.loc[out["Partner ID"].eq(""), "Drop Off Date"] = row_drop.loc[out["Partner ID"].eq("")]

    for col in ("Latest Allocation Date", "Drop Off Date"):
        out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d")
        out[col] = out[col].replace({"NaT": ""}).fillna("")

    for c in empty_cols:
        if c not in out.columns:
            out[c] = "" if c != "Has Allocation Row" else False
    return out[empty_cols].reset_index(drop=True)


def map_final_status_group(value) -> str:
    """
    Active / Allocation / Same Day D&A → Onroad
    RFD / New Deployment → RFD
    everything else unchanged
    """
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return "Blank"
    key = text.casefold()
    if key in {"active", "allocation", "same day d&a", "same day d and a"}:
        return "Onroad"
    if key in {"rfd", "new deployment"}:
        return "RFD"
    return text


def final_status_daily_counts(
    start_date: str,
    end_date: str,
    partner: str | None = None,
    type_name: str | None = None,
    folder: Path | None = None,
) -> pd.DataFrame:
    """Day-wise vehicle counts by remapped Final Status (for line chart)."""
    alloc, _ = load_allocation(folder)
    if alloc.empty or "Final Status" not in alloc.columns:
        return pd.DataFrame(columns=["Date", "Status", "Count"])

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        start, end = end, start

    df = alloc[(alloc["Date"] >= start) & (alloc["Date"] <= end)].copy()
    if partner and partner not in {"All partners", ""}:
        df = df[df["partner Name"].fillna("").astype(str).str.strip() == partner]
    if type_name and type_name not in {"All types", ""}:
        df = df[df["Type"].fillna("").astype(str).str.strip() == type_name]

    if df.empty:
        return pd.DataFrame(columns=["Date", "Status", "Count"])

    df["Status"] = df["Final Status"].map(map_final_status_group)
    daily = (
        df.groupby(["Date", "Status"], as_index=False)
        .agg(Count=("Vehicle Number", "nunique"))
        .sort_values(["Date", "Status"])
    )
    daily["Date"] = daily["Date"].dt.strftime("%Y-%m-%d")
    return daily.reset_index(drop=True)
