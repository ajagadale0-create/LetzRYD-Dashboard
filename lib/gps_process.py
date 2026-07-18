"""GPS day-wise KM loader — keyed by Vehicle Number + Date."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from lib.allocation import _norm_vehicle


def gps_dir(base: Path | None = None) -> Path:
    root = Path(base) if base else Path(__file__).resolve().parents[1]
    for name in ("GPS", "Gps", "gps"):
        path = root / name
        if path.is_dir():
            return path
    return root / "GPS"


def list_gps_files(folder: Path | None = None) -> list[Path]:
    folder = folder if folder is not None else gps_dir()
    if not folder.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(folder.glob("*.xlsx")) + sorted(folder.glob("*.csv")):
        if path.name.startswith("~$"):
            continue
        files.append(path)
    return files


def gps_fingerprint(folder: Path | None = None) -> str:
    parts: list[str] = []
    for path in list_gps_files(folder):
        try:
            stat = path.stat()
            parts.append(f"gps:{path.name}:{stat.st_size}")
        except OSError:
            continue
    return "|".join(parts)


def _load_one(path: Path) -> pd.DataFrame:
    try:
        if path.suffix.lower() in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path, dtype=str)
    except (PermissionError, OSError, ValueError):
        return pd.DataFrame(columns=["Vehicle Number", "Date", "GPS KMs"])

    lower_map = {str(c).strip().lower(): c for c in df.columns}
    rename = {}
    wanted = {
        "vehicle number": "Vehicle Number",
        "vehicle": "Vehicle Number",
        "car number": "Vehicle Number",
        "number plate": "Vehicle Number",
        "date": "Date",
        "kms": "GPS KMs",
        "km": "GPS KMs",
        "gps kms": "GPS KMs",
        "gps km": "GPS KMs",
    }
    for key, canon in wanted.items():
        if key in lower_map:
            rename[lower_map[key]] = canon
    df = df.rename(columns=rename)

    if "Vehicle Number" not in df.columns or "Date" not in df.columns:
        return pd.DataFrame(columns=["Vehicle Number", "Date", "GPS KMs"])

    out = pd.DataFrame()
    out["Vehicle Number"] = df["Vehicle Number"].map(_norm_vehicle)
    out["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    if "GPS KMs" in df.columns:
        out["GPS KMs"] = pd.to_numeric(df["GPS KMs"], errors="coerce").fillna(0)
    else:
        out["GPS KMs"] = 0.0

    out = out[(out["Vehicle Number"] != "") & out["Date"].notna()].copy()
    # One row per vehicle-day (sum if duplicates)
    out = (
        out.groupby(["Vehicle Number", "Date"], as_index=False)
        .agg(**{"GPS KMs": ("GPS KMs", "sum")})
    )
    out["GPS KMs"] = out["GPS KMs"].round(2)
    return out


def load_gps_days(folder: Path | None = None) -> pd.DataFrame:
    """All GPS vehicle×day KM rows from GPS folder."""
    frames = [_load_one(p) for p in list_gps_files(folder)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["Vehicle Number", "Date", "GPS KMs"])
    all_df = pd.concat(frames, ignore_index=True)
    return (
        all_df.groupby(["Vehicle Number", "Date"], as_index=False)
        .agg(**{"GPS KMs": ("GPS KMs", "sum")})
        .sort_values(["Date", "Vehicle Number"])
        .reset_index(drop=True)
    )


def build_gps_vehicle_days(folder: Path | None = None) -> tuple[pd.DataFrame, dict]:
    days = load_gps_days(folder)
    meta = {
        "files": len(list_gps_files(folder)),
        "rows": int(len(days)),
        "vehicles": int(days["Vehicle Number"].nunique()) if len(days) else 0,
        "date_from": days["Date"].min().strftime("%Y-%m-%d") if len(days) else "",
        "date_to": days["Date"].max().strftime("%Y-%m-%d") if len(days) else "",
    }
    return days, meta
