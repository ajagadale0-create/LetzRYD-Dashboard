"""
Sync heavy dashboard data from Google Drive → local cache.

Local PC (G: Drive already synced): no-op, uses project folder.
Streamlit Cloud: if secrets have gcp_service_account + drive.root_folder_id,
downloads Uber/OLA/GPS/Rapido/Allocation into .data_cache/ (only when changed).

Secrets example (.streamlit/secrets.toml on Cloud):

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
# Use TOML multiline quotes for the PEM key (BEGIN…END PRIVATE KEY)
private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
client_email = "dashboard@....iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."

[drive]
# Folder ID of "AI Dashboard" on Drive (from folder URL)
root_folder_id = "1xxxxxxxxxxxxxxx"
# Optional: separate "Rapido Data" folder ID (Pan India files)
rapido_data_folder_id = ""
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

from lib.paths import code_root, data_root

CACHE_DIRNAME = ".data_cache"
META_NAME = ".drive_sync_meta.json"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Subfolders under AI Dashboard root to sync (name → relative path)
SYNC_CHILDREN = {
    "Uber": "Uber",
    "OLA": "OLA",
    "Ola": "OLA",
    "GPS": "GPS",
    "Rapido": "Rapido",
    "Vehicle Allocation Status": "Vehicle Allocation Status",
    "Allocation & Drop off form": "Allocation & Drop off form",
}

SKIP_NAME_PREFIXES = ("~$",)
SKIP_DIR_NAMES = {"output", ".cache", "__pycache__", ".git"}
ALLOW_SUFFIXES = {".csv", ".xlsx", ".xls"}


def drive_configured() -> bool:
    try:
        import streamlit as st

        drive = st.secrets.get("drive", {})
        gcp = st.secrets.get("gcp_service_account", {})
        return bool(drive.get("root_folder_id")) and bool(gcp.get("client_email"))
    except Exception:
        return False


def cache_dir() -> Path:
    return code_root() / CACHE_DIRNAME


def _secrets_dict() -> dict[str, Any]:
    import streamlit as st

    return {
        "drive": dict(st.secrets.get("drive", {})),
        "gcp": dict(st.secrets.get("gcp_service_account", {})),
    }


def _normalize_private_key(raw: Any) -> str:
    """Fix common Streamlit-secrets paste issues for PEM private keys."""
    key = str(raw).strip().strip('"').strip("'")
    # TOML / JSON often store newlines as the two chars \n
    if "\\n" in key and "-----BEGIN" in key:
        key = key.replace("\\n", "\n")
    key = key.replace("\r\n", "\n").replace("\r", "\n")
    # Accidental double-escaping
    while "\\n" in key and "BEGIN PRIVATE KEY" in key.split("\n", 1)[0]:
        key = key.replace("\\n", "\n")
    if "BEGIN PRIVATE KEY" not in key or "END PRIVATE KEY" not in key:
        raise ValueError(
            "Secrets private_key is incomplete. "
            "In Streamlit Secrets, paste private_key with triple quotes "
            '(see app Manage → Secrets help).'
        )
    return key


def _drive_service():
    import socket

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    # Prevent infinite hang on Drive download/export
    try:
        socket.setdefaulttimeout(90)
    except Exception:
        pass

    info = _secrets_dict()["gcp"]
    creds_info = {k: info[k] for k in info}
    if "private_key" not in creds_info:
        raise ValueError(
            "Missing gcp_service_account.private_key in Streamlit Secrets."
        )
    try:
        creds_info["private_key"] = _normalize_private_key(creds_info["private_key"])
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES
        )
    except Exception as exc:
        raise ValueError(
            "Google service-account private_key in Secrets is invalid. "
            "Open the JSON key file, copy private_key again using triple quotes "
            'in Secrets, e.g. private_key = """-----BEGIN...-----END...""". '
            f"Detail: {type(exc).__name__}"
        ) from None

    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp

        http = AuthorizedHttp(creds, http=httplib2.Http(timeout=90))
        return build("drive", "v3", http=http, cache_discovery=False)
    except Exception:
        return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_children(service, folder_id: str) -> list[dict]:
    items: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                pageSize=200,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        items.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def _is_folder(item: dict) -> bool:
    return item.get("mimeType") == "application/vnd.google-apps.folder"


def _should_skip_file(name: str) -> bool:
    lower = name.lower()
    if any(name.startswith(p) for p in SKIP_NAME_PREFIXES):
        return True
    suf = Path(name).suffix.lower()
    return suf not in ALLOW_SUFFIXES


def _walk_files(
    service, folder_id: str, rel: Path, out: list[tuple[dict, Path]]
) -> None:
    for item in _list_children(service, folder_id):
        name = item.get("name") or ""
        if _is_folder(item):
            if name.lower() in SKIP_DIR_NAMES:
                continue
            _walk_files(service, item["id"], rel / name, out)
        else:
            # Google Sheets: export to XLSX during sync (so importerange results are fetched).
            if item.get("mimeType") == "application/vnd.google-apps.spreadsheet":
                out.append((item, rel / name))
                continue
            if _should_skip_file(name):
                continue
            out.append((item, rel / name))


def _load_meta(meta_path: Path) -> dict:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_meta(meta_path: Path, meta: dict) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _download_file(service, file_id: str, dest: Path) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    dest.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    with open(tmp, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    tmp.replace(dest)


def _export_spreadsheet_xlsx(service, file_id: str, dest: Path) -> None:
    """Export a Drive Google Sheet as XLSX into dest."""
    from googleapiclient.http import MediaIoBaseDownload

    dest.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().export(
        fileId=file_id,
        mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    tmp = dest.with_suffix(dest.suffix + ".partial")
    with open(tmp, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    tmp.replace(dest)


def sync_drive_data(*, force: bool = False) -> dict:
    """
    Pull Drive folders into .data_cache and set AI_DASHBOARD_DATA_ROOT.
    Returns status dict for UI.
    """
    if not drive_configured():
        os.environ.pop("AI_DASHBOARD_DATA_ROOT", None)
        return {
            "ok": True,
            "mode": "local",
            "message": "Drive secrets not set — using local folders",
            "files": 0,
            "downloaded": 0,
            "root": str(data_root()),
        }

    secrets = _secrets_dict()
    root_id = str(secrets["drive"].get("root_folder_id", "")).strip()
    rapido_extra = str(secrets["drive"].get("rapido_data_folder_id", "")).strip()

    dest_root = cache_dir()
    dest_root.mkdir(parents=True, exist_ok=True)
    meta_path = dest_root / META_NAME
    meta = {} if force else _load_meta(meta_path)
    service = _drive_service()

    # Map top-level children of AI Dashboard folder
    top = _list_children(service, root_id)
    by_name = { (i.get("name") or ""): i for i in top if _is_folder(i) }

    planned: list[tuple[dict, Path]] = []
    for drive_name, local_rel in SYNC_CHILDREN.items():
        item = by_name.get(drive_name)
        if not item:
            continue
        _walk_files(service, item["id"], Path(local_rel), planned)

    # Optional Rapido Data → Rapido/ (or Rapido/Pan India)
    if rapido_extra:
        _walk_files(service, rapido_extra, Path("Rapido") / "Pan India", planned)
    else:
        # Sibling-style: if root parent listing not available, try child "Rapido Data"
        rd = by_name.get("Rapido Data")
        if rd:
            _walk_files(service, rd["id"], Path("Rapido") / "Pan India", planned)

    downloaded = 0
    skipped = 0
    errors: list[str] = []

    for item, rel in planned:
        fid = item["id"]
        size = str(item.get("size") or "0")
        mtime = str(item.get("modifiedTime") or "")
        key = fid
        prev = meta.get(key, {})
        dest = dest_root / rel
        is_sheet = item.get("mimeType") == "application/vnd.google-apps.spreadsheet"
        # Exported XLSX should overwrite the cached file.
        if is_sheet:
            # Clean trailing spaces from Google Sheet titles (e.g. "Pan India Allocation ")
            clean_name = (item.get("name") or rel.name).strip() or rel.stem
            dest = dest_root / rel.parent / f"{clean_name}.xlsx"
        need = force or not dest.exists() or prev.get("modifiedTime") != mtime
        if not need:
            skipped += 1
            continue
        try:
            if is_sheet:
                _export_spreadsheet_xlsx(service, fid, dest)
            else:
                _download_file(service, fid, dest)
            meta[key] = {
                "name": item.get("name"),
                "path": str(dest.relative_to(dest_root)).replace("\\", "/"),
                "size": size,
                "modifiedTime": mtime,
            }
            downloaded += 1
        except Exception as exc:  # noqa: BLE001 — surface in UI
            errors.append(f"{rel}: {exc}")

    _save_meta(meta_path, meta)
    os.environ["AI_DASHBOARD_DATA_ROOT"] = str(dest_root)

    return {
        "ok": len(errors) == 0,
        "mode": "drive",
        "message": "Synced from Google Drive" if downloaded or skipped else "No matching files on Drive",
        "files": len(planned),
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors[:8],
        "root": str(dest_root),
        "client_email": secrets["gcp"].get("client_email", ""),
    }


def ensure_data_ready(*, force: bool = False, show_status: bool = True) -> dict:
    """
    Call once at app start.
    Local without secrets → local folders.
    Cloud: refresh small Partner/Pan India sheets; full Uber/OLA sync only when
    force=True (Sync Drive now) or cache is empty.
    """
    if not drive_configured():
        os.environ.pop("AI_DASHBOARD_DATA_ROOT", None)
        return {
            "ok": True,
            "mode": "local",
            "message": "Local data folders",
            "files": 0,
            "downloaded": 0,
            "root": str(code_root()),
        }

    dest_root = cache_dir()
    dest_root.mkdir(parents=True, exist_ok=True)
    os.environ["AI_DASHBOARD_DATA_ROOT"] = str(dest_root)

    meta_path = dest_root / META_NAME
    cache_ok = meta_path.exists() and any(
        (dest_root / name).exists()
        for name in ("Uber", "Vehicle Allocation Status", "OLA", "GPS")
    )

    def _light_info(form_sync: dict) -> dict:
        return {
            "ok": True,
            "mode": "drive-cache",
            "message": "Using cached Drive data (Partner/Pan India refreshed)",
            "files": 0,
            "downloaded": 0,
            "skipped": 0,
            "root": str(dest_root),
            "form_sheets": form_sync,
            "fast_boot": True,
        }

    def _run_sync() -> dict:
        form_sync = sync_allocation_form_sheets(force=True)
        if force or not cache_ok:
            info = sync_drive_data(force=force)
            info["form_sheets"] = form_sync
            info["fast_boot"] = False
            return info
        return _light_info(form_sync)

    if show_status:
        try:
            import streamlit as st

            with st.status("Starting dashboard…", expanded=True) as status:
                st.write("Refreshing Partner + Pan India (fast)…")
                form_sync = sync_allocation_form_sheets(force=True)
                if form_sync.get("synced"):
                    st.write("Sheets: " + ", ".join(form_sync["synced"]))
                elif form_sync.get("details"):
                    for hint, det in (form_sync.get("details") or {}).items():
                        st.write(f"{hint}: {det.get('message', '')}")

                if force or not cache_ok:
                    st.write("Full Drive sync (Uber/OLA/GPS) — first time / forced…")
                    info = sync_drive_data(force=force)
                    info["form_sheets"] = form_sync
                    info["fast_boot"] = False
                else:
                    st.write(
                        "Using cached Uber/OLA/GPS — "
                        "click Sync Drive now only when those files change."
                    )
                    info = _light_info(form_sync)

                if info.get("errors"):
                    st.write("Some files failed:")
                    for e in info["errors"][:8]:
                        st.write(f"- {e}")
                st.write(
                    f"Downloaded: {info.get('downloaded', 0)} · "
                    f"Cached/skipped: {info.get('skipped', 0)} · "
                    f"fast_boot={info.get('fast_boot')}"
                )
                status.update(label="Ready", state="complete")
                return info
        except Exception as exc:
            info = _run_sync()
            info["status_error"] = str(exc)
            return info
    return _run_sync()


def sync_named_sheet_from_drive(
    *,
    name_hint: str,
    dest_name: str,
    force: bool = False,
) -> dict:
    """
    Find a Google Sheet under AI Dashboard → Allocation & Drop off form
    and export it to .data_cache (or data_root) as XLSX.
    User never exports Excel — service account does it.
    """
    if not drive_configured():
        return {
            "ok": False,
            "message": "Drive secrets not set",
            "path": "",
            "bytes": 0,
        }

    secrets = _secrets_dict()
    root_id = str(secrets["drive"].get("root_folder_id", "")).strip()
    if not root_id:
        return {"ok": False, "message": "Missing drive.root_folder_id", "path": "", "bytes": 0}

    try:
        service = _drive_service()
        top = _list_children(service, root_id)
        form_folder = None
        for item in top:
            name = (item.get("name") or "").strip().casefold()
            if _is_folder(item) and "allocation" in name and "drop" in name:
                form_folder = item
                break
        if not form_folder:
            return {
                "ok": False,
                "message": "Allocation & Drop off form folder not found on Drive",
                "path": "",
                "bytes": 0,
            }

        hint = name_hint.strip().casefold()
        sheet_item = None
        for item in _list_children(service, form_folder["id"]):
            name = (item.get("name") or "").strip().casefold()
            mime = item.get("mimeType") or ""
            if mime == "application/vnd.google-apps.spreadsheet" and hint in name:
                sheet_item = item
                break
        if not sheet_item:
            return {
                "ok": False,
                "message": f"Google Sheet matching '{name_hint}' not found",
                "path": "",
                "bytes": 0,
            }

        dest_root = cache_dir()
        dest = dest_root / "Allocation & Drop off form" / dest_name
        meta_path = dest_root / META_NAME
        meta = _load_meta(meta_path)
        key = sheet_item["id"]
        mtime = str(sheet_item.get("modifiedTime") or "")
        prev = meta.get(key, {})
        if (
            not force
            and dest.exists()
            and prev.get("modifiedTime") == mtime
        ):
            os.environ["AI_DASHBOARD_DATA_ROOT"] = str(dest_root)
            return {
                "ok": True,
                "message": f"Cached {dest.name}",
                "path": str(dest),
                "bytes": dest.stat().st_size,
                "skipped": True,
                "sheet_id": key,
            }

        _export_spreadsheet_xlsx(service, key, dest)
        meta[key] = {
            "name": sheet_item.get("name"),
            "path": str(dest.relative_to(dest_root)).replace("\\", "/"),
            "size": str(dest.stat().st_size),
            "modifiedTime": mtime,
        }
        _save_meta(meta_path, meta)
        os.environ["AI_DASHBOARD_DATA_ROOT"] = str(dest_root)
        return {
            "ok": True,
            "message": f"Auto-synced {dest.name}",
            "path": str(dest),
            "bytes": dest.stat().st_size,
            "skipped": False,
            "sheet_id": key,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Auto-sync failed: {exc}",
            "path": "",
            "bytes": 0,
        }


def sync_allocation_form_sheets(*, force: bool = False) -> dict:
    """Auto-sync Partner Details + Pan India Allocation Google Sheets."""
    results = {}
    synced = []
    for hint, dest in (
        ("partner details", "Partner Details.xlsx"),
        ("pan india allocation", "Pan India Allocation.xlsx"),
    ):
        info = sync_named_sheet_from_drive(
            name_hint=hint, dest_name=dest, force=force
        )
        results[hint] = info
        if info.get("ok"):
            synced.append(dest)
    return {"ok": all(v.get("ok") for v in results.values()), "synced": synced, "details": results}
