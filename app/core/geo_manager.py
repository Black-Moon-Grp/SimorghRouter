from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MIN_DAT_BYTES = 1024  # 1 KB minimum sanity check
MAX_DAT_BYTES = 100 * 1024 * 1024  # 100 MB safety cap


def _validate_dat_bytes(data: bytes, filename: str) -> None:
    if len(data) < MIN_DAT_BYTES:
        raise ValueError(f"{filename} is too small ({len(data)} bytes); likely invalid or HTML error page")
    if len(data) > MAX_DAT_BYTES:
        raise ValueError(f"{filename} exceeds 100 MB limit")
    head = data[:64].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise ValueError(f"{filename} appears to be an HTML page instead of a binary .dat file")


async def download_dat_file(url: str, target_name: str) -> int:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme for {target_name}: must be http or https")

    settings.assets_path.mkdir(parents=True, exist_ok=True)
    target_path = settings.assets_path / target_name
    tmp_path = settings.assets_path / f"{target_name}.tmp"

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(url.strip(), headers={"User-Agent": "v2ray-smart-router/1.0"})
        resp.raise_for_status()
        data = resp.content

    _validate_dat_bytes(data, target_name)
    tmp_path.write_bytes(data)
    tmp_path.replace(target_path)
    logger.info("Updated %s (%d bytes) from %s", target_path, len(data), url)
    return len(data)


def save_uploaded_dat(data: bytes, target_name: str) -> int:
    if target_name not in ("geoip.dat", "geosite.dat"):
        raise ValueError("Only geoip.dat or geosite.dat uploads are permitted")
    _validate_dat_bytes(data, target_name)
    settings.assets_path.mkdir(parents=True, exist_ok=True)
    target_path = settings.assets_path / target_name
    tmp_path = settings.assets_path / f"{target_name}.tmp"
    tmp_path.write_bytes(data)
    tmp_path.replace(target_path)
    logger.info("Saved uploaded %s (%d bytes)", target_path, len(data))
    return len(data)


def get_dat_file_info(filename: str) -> tuple[bool, float, Optional[datetime]]:
    path: Path = settings.assets_path / filename
    if not path.is_file():
        return False, 0.0, None
    stat = path.stat()
    size_mb = round(stat.st_size / (1024 * 1024), 2)
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return True, size_mb, mtime
