"""Ola RawCrns loader — completed trips only, keyed by car + date."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from lib.allocation import _norm_vehicle

RAW_SHEET = "RawCrns"
KEEP_COLS = [
    "Date",
    "Car number",
    "Customer Bill Raw",
    "Cash collected by driver Raw",
    "Actual Kms Raw",
    "Trip Time Raw",
    "Completion Status",
]


def ola_dir(base: Path | None = None) -> Path:
    if base is not None:
        root = Path(base)
    else:
        from lib.paths import data_root

        root = data_root()
    # Prefer OLA; fall back to Ola
    for name in ("OLA", "Ola", "ola"):
        path = root / name
        if path.is_dir():
            return path
    return root / "OLA"


def list_ola_files(folder: Path | None = None) -> list[Path]:
    folder = folder if folder is not None else ola_dir()
    if not folder.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(folder.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        files.append(path)
    return files


def ola_fingerprint(folder: Path | None = None) -> str:
    parts: list[str] = []
    for path in list_ola_files(folder):
        try:
            stat = path.stat()
            parts.append(f"ola:{path.name}:{stat.st_size}:{int(stat.st_mtime)}")
        except OSError:
            continue
    return "|".join(parts)


def _load_rawcrns(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name=RAW_SHEET)
    except (ValueError, PermissionError, OSError):
        return pd.DataFrame(columns=KEEP_COLS)

    rename = {c: c for c in df.columns}
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    wanted = {
        "date": "Date",
        "car number": "Car number",
        "customer bill raw": "Customer Bill Raw",
        "cash collected by driver raw": "Cash collected by driver Raw",
        "actual kms raw": "Actual Kms Raw",
        "trip time raw": "Trip Time Raw",
        "completion status": "Completion Status",
    }
    for key, canon in wanted.items():
        if key in lower_map:
            rename[lower_map[key]] = canon
    df = df.rename(columns=rename)

    missing = [c for c in KEEP_COLS if c not in df.columns]
    if missing:
        return pd.DataFrame(columns=KEEP_COLS)

    out = df[KEEP_COLS].copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    out["Vehicle Number"] = out["Car number"].map(_norm_vehicle)
    out["Completion Status"] = (
        out["Completion Status"].fillna("").astype(str).str.strip().str.lower()
    )
    out = out[out["Completion Status"] == "completed"].copy()
    out = out[out["Vehicle Number"] != ""].copy()
    out = out[out["Date"].notna()].copy()

    for col in (
        "Customer Bill Raw",
        "Cash collected by driver Raw",
        "Actual Kms Raw",
        "Trip Time Raw",
    ):
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    # Ola stores driver cash as negative (same idea as Uber) — use absolute for collection
    out["Cash collected by driver Raw"] = out["Cash collected by driver Raw"].abs()

    return out


def load_ola_rawcrns(folder: Path | None = None) -> pd.DataFrame:
    """All completed RawCrns rows from every Ola workbook."""
    frames = [_load_rawcrns(p) for p in list_ola_files(folder)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(
            columns=[
                "Date",
                "Vehicle Number",
                "Customer Bill Raw",
                "Cash collected by driver Raw",
                "Actual Kms Raw",
                "Trip Time Raw",
            ]
        )
    return pd.concat(frames, ignore_index=True)


def build_ola_vehicle_days(folder: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Vehicle × calendar day Ola metrics (completed RawCrns only)."""
    raw = load_ola_rawcrns(folder)
    empty = pd.DataFrame(
        columns=[
            "Vehicle Number",
            "Date",
            "Ola Customer Bill",
            "Ola Cash Collected",
            "Ola Actual Kms",
            "Ola Trip Time",
            "Ola Trips",
        ]
    )
    if raw.empty:
        return empty, {"files": 0, "rows": 0, "vehicles": 0}

    days = (
        raw.groupby(["Vehicle Number", "Date"], as_index=False)
        .agg(
            **{
                "Ola Customer Bill": ("Customer Bill Raw", "sum"),
                "Ola Cash Collected": ("Cash collected by driver Raw", "sum"),
                "Ola Actual Kms": ("Actual Kms Raw", "sum"),
                "Ola Trip Time": ("Trip Time Raw", "sum"),
                "Ola Trips": ("Customer Bill Raw", "count"),
            }
        )
    )
    days["Ola Customer Bill"] = days["Ola Customer Bill"].round(2)
    days["Ola Cash Collected"] = days["Ola Cash Collected"].round(2)
    days["Ola Actual Kms"] = days["Ola Actual Kms"].round(2)
    days["Ola Trip Time"] = days["Ola Trip Time"].round(0).astype(int)
    days["Ola Trips"] = days["Ola Trips"].astype(int)
    meta = {
        "files": len(list_ola_files(folder)),
        "rows": int(len(days)),
        "vehicles": int(days["Vehicle Number"].nunique()),
        "date_from": days["Date"].min().strftime("%Y-%m-%d"),
        "date_to": days["Date"].max().strftime("%Y-%m-%d"),
    }
    return days, meta


def build_ola_vehicle_summary(
    date_from: str,
    date_to: str,
    folder: Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Sum completed Ola metrics per vehicle for [date_from, date_to].
    Match key = Vehicle Number (normalized Car number).
    """
    days, day_meta = build_ola_vehicle_days(folder)
    start = pd.Timestamp(date_from).normalize()
    end = pd.Timestamp(date_to).normalize()
    if end < start:
        start, end = end, start

    empty = pd.DataFrame(
        columns=[
            "Vehicle Number",
            "Ola Customer Bill",
            "Ola Cash Collected",
            "Ola Actual Kms",
            "Ola Trip Time",
            "Ola Trips",
        ]
    )
    if days.empty:
        return empty, {
            "files": day_meta.get("files", 0),
            "rows_completed": 0,
            "vehicles": 0,
            "date_from": start.strftime("%Y-%m-%d"),
            "date_to": end.strftime("%Y-%m-%d"),
        }

    day = days[(days["Date"] >= start) & (days["Date"] <= end)].copy()
    if day.empty:
        return empty, {
            "files": day_meta.get("files", 0),
            "rows_completed": 0,
            "vehicles": 0,
            "date_from": start.strftime("%Y-%m-%d"),
            "date_to": end.strftime("%Y-%m-%d"),
        }

    summary = (
        day.groupby("Vehicle Number", as_index=False)
        .agg(
            **{
                "Ola Customer Bill": ("Ola Customer Bill", "sum"),
                "Ola Cash Collected": ("Ola Cash Collected", "sum"),
                "Ola Actual Kms": ("Ola Actual Kms", "sum"),
                "Ola Trip Time": ("Ola Trip Time", "sum"),
                "Ola Trips": ("Ola Trips", "sum"),
            }
        )
    )
    summary["Ola Customer Bill"] = summary["Ola Customer Bill"].round(2)
    summary["Ola Cash Collected"] = summary["Ola Cash Collected"].round(2)
    summary["Ola Actual Kms"] = summary["Ola Actual Kms"].round(2)
    summary["Ola Trip Time"] = summary["Ola Trip Time"].round(0).astype(int)
    summary["Ola Trips"] = summary["Ola Trips"].astype(int)

    meta = {
        "files": day_meta.get("files", 0),
        "rows_completed": int(day["Ola Trips"].sum()),
        "vehicles": int(len(summary)),
        "date_from": start.strftime("%Y-%m-%d"),
        "date_to": end.strftime("%Y-%m-%d"),
    }
    return summary, meta
