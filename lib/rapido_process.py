"""Rapido Trip Level loader — vehicle × calendar day (no 4am rule)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from lib.allocation import _norm_vehicle

TRIP_SHEET = "Trip Level"
RAW_COLS = [
    "yyyymmdd",
    "captain_obj_current_vehicle_number",
    "captain_earnings",
    "toll_charges",
    "ride_time",
]


def rapido_dir(base: Path | None = None) -> Path:
    if base is not None:
        root = Path(base)
    else:
        from lib.paths import data_root

        root = data_root()
    for name in ("Rapido", "rapido"):
        path = root / name
        if path.is_dir():
            return path
    return root / "Rapido"


def _extra_rapido_folders(base: Path | None = None) -> list[Path]:
    """Also scan Rapido/Pan India and sibling Rapido Data when present."""
    from lib.paths import data_root, code_root

    root = Path(base) if base is not None else data_root()
    code = code_root()
    extras = [
        root / "Rapido" / "Pan India",
        root / "Rapido Data" / "Pan India",
        root / "Rapido Data",
        code.parent / "Rapido Data" / "Pan India",
        code.parent / "Rapido Data",
    ]
    return [p for p in extras if p.is_dir()]


def list_rapido_files(folder: Path | None = None) -> list[Path]:
    folders: list[Path] = []
    primary = folder if folder is not None else rapido_dir()
    folders.append(primary)
    if folder is None:
        folders.extend(_extra_rapido_folders())

    seen: set[str] = set()
    files: list[Path] = []
    for folder in folders:
        for path in sorted(folder.glob("*.xlsx")) + sorted(folder.glob("*.xls")):
            if path.name.startswith("~$"):
                continue
            key = path.name.lower()
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files


def rapido_fingerprint(folder: Path | None = None) -> str:
    parts: list[str] = []
    for path in list_rapido_files(folder):
        try:
            parts.append(f"rapido:{path.name}:{path.stat().st_size}")
        except OSError:
            continue
    return "|".join(parts)


def _parse_yyyymmdd(series: pd.Series) -> pd.Series:
    """20260717 / '20260717' → normalized Timestamp (calendar date)."""
    text = series.astype(str).str.strip()
    text = text.str.replace(r"\.0$", "", regex=True)
    # Prefer strict yyyymmdd
    parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    # Fallback for any other date-like values
    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(text.loc[missing], errors="coerce")
    return parsed.dt.normalize()


def _load_trip_level(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name=TRIP_SHEET)
    except (ValueError, KeyError, PermissionError, OSError):
        # Sheet name variants
        try:
            xl = pd.ExcelFile(path)
            match = next(
                (s for s in xl.sheet_names if s.strip().lower() == "trip level"),
                None,
            )
            if not match:
                return pd.DataFrame(columns=RAW_COLS)
            df = pd.read_excel(path, sheet_name=match)
        except (ValueError, PermissionError, OSError):
            return pd.DataFrame(columns=RAW_COLS)

    lower_map = {str(c).strip().lower(): c for c in df.columns}
    rename = {}
    wanted = {
        "yyyymmdd": "yyyymmdd",
        "captain_obj_current_vehicle_number": "captain_obj_current_vehicle_number",
        "captain_earnings": "captain_earnings",
        "toll_charges": "toll_charges",
        "ride_time": "ride_time",
    }
    for key, canon in wanted.items():
        if key in lower_map:
            rename[lower_map[key]] = canon
    df = df.rename(columns=rename)

    missing = [c for c in ("yyyymmdd", "captain_obj_current_vehicle_number") if c not in df.columns]
    if missing:
        return pd.DataFrame(columns=RAW_COLS)

    out = pd.DataFrame()
    out["Date"] = _parse_yyyymmdd(df["yyyymmdd"])
    out["Vehicle Number"] = df["captain_obj_current_vehicle_number"].map(_norm_vehicle)
    out["captain_earnings"] = pd.to_numeric(
        df["captain_earnings"] if "captain_earnings" in df.columns else 0,
        errors="coerce",
    ).fillna(0)
    out["toll_charges"] = pd.to_numeric(
        df["toll_charges"] if "toll_charges" in df.columns else 0,
        errors="coerce",
    ).fillna(0)
    out["ride_time"] = pd.to_numeric(
        df["ride_time"] if "ride_time" in df.columns else 0,
        errors="coerce",
    ).fillna(0)
    out["Rapido Revenue"] = (out["captain_earnings"] + out["toll_charges"]).round(2)

    out = out[out["Vehicle Number"] != ""].copy()
    out = out[out["Date"].notna()].copy()
    return out


def build_rapido_vehicle_days(
    folder: Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Vehicle × calendar day: Rapido Revenue, Ride Time, Trips (row count)."""
    empty = pd.DataFrame(
        columns=[
            "Vehicle Number",
            "Date",
            "Rapido Revenue",
            "Rapido Ride Time",
            "Rapido Trips",
        ]
    )
    files = list_rapido_files(folder)
    frames = [_load_trip_level(p) for p in files]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return empty, {"files": 0, "rows": 0, "vehicles": 0}

    raw = pd.concat(frames, ignore_index=True)
    days = (
        raw.groupby(["Vehicle Number", "Date"], as_index=False)
        .agg(
            **{
                "Rapido Revenue": ("Rapido Revenue", "sum"),
                "Rapido Ride Time": ("ride_time", "sum"),
                "Rapido Trips": ("Rapido Revenue", "count"),
            }
        )
    )
    days["Rapido Revenue"] = days["Rapido Revenue"].round(2)
    days["Rapido Ride Time"] = days["Rapido Ride Time"].round(2)
    days["Rapido Trips"] = days["Rapido Trips"].astype(int)
    meta = {
        "files": len(files),
        "rows": int(len(days)),
        "vehicles": int(days["Vehicle Number"].nunique()),
        "date_from": days["Date"].min().strftime("%Y-%m-%d"),
        "date_to": days["Date"].max().strftime("%Y-%m-%d"),
    }
    return days, meta
