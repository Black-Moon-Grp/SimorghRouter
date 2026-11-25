from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.config import settings
from app.core.geo_manager import download_dat_file, get_dat_file_info, save_uploaded_dat
from app.core.xray_manager import xray_manager
from app.database import get_db
from app.models import AdminUser, SystemSetting
from app.schemas import GeoStatusResponse, GeoUpdateRequest

router = APIRouter(prefix="/api/geo", tags=["geo"])


async def _upsert_setting(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(SystemSetting, key)
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))


@router.get("/status", response_model=GeoStatusResponse)
async def geo_status(
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    ip_exists, ip_size, ip_mtime = get_dat_file_info("geoip.dat")
    site_exists, site_size, site_mtime = get_dat_file_info("geosite.dat")

    ip_url_row = await db.get(SystemSetting, "geoip_url")
    site_url_row = await db.get(SystemSetting, "geosite_url")
    weekly_row = await db.get(SystemSetting, "geo_weekly_auto_update")

    return GeoStatusResponse(
        geoip_exists=ip_exists,
        geoip_size_mb=ip_size,
        geoip_updated_at=ip_mtime,
        geosite_exists=site_exists,
        geosite_size_mb=site_size,
        geosite_updated_at=site_mtime,
        geoip_url=ip_url_row.value if ip_url_row else settings.GEOIP_URL,
        geosite_url=site_url_row.value if site_url_row else settings.GEOSITE_URL,
        weekly_auto_update=(weekly_row.value == "true") if weekly_row else True,
    )


@router.post("/update")
async def update_geo_databases(
    payload: GeoUpdateRequest,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    ip_url_row = await db.get(SystemSetting, "geoip_url")
    site_url_row = await db.get(SystemSetting, "geosite_url")

    geoip_url = payload.geoip_url or (ip_url_row.value if ip_url_row else settings.GEOIP_URL)
    geosite_url = payload.geosite_url or (site_url_row.value if site_url_row else settings.GEOSITE_URL)

    if payload.geoip_url:
        await _upsert_setting(db, "geoip_url", payload.geoip_url.strip())
    if payload.geosite_url:
        await _upsert_setting(db, "geosite_url", payload.geosite_url.strip())
    if payload.weekly_auto_update is not None:
        await _upsert_setting(db, "geo_weekly_auto_update", "true" if payload.weekly_auto_update else "false")
    await db.commit()

    try:
        ip_bytes = await download_dat_file(geoip_url, "geoip.dat")
        site_bytes = await download_dat_file(geosite_url, "geosite.dat")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Geo database download failed: {exc}")

    await xray_manager.start_or_reload_main()
    return {
        "status": "updated",
        "geoip_bytes": ip_bytes,
        "geosite_bytes": site_bytes,
    }


@router.post("/upload/{target_name}")
async def upload_geo_file(
    target_name: str,
    file: UploadFile = File(...),
    _: AdminUser = Depends(get_current_admin),
):
    if target_name not in ("geoip.dat", "geosite.dat"):
        raise HTTPException(status_code=400, detail="Target must be geoip.dat or geosite.dat")
    data = await file.read()
    try:
        size = save_uploaded_dat(data, target_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await xray_manager.start_or_reload_main()
    return {"status": "uploaded", "file": target_name, "bytes": size}
