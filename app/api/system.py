from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.config import settings
from app.core.scheduler import router_scheduler
from app.core.xray_manager import xray_manager
from app.database import get_db
from app.models import AdminUser, ClientAccount, OutboundConfig, SystemSetting
from app.schemas import SystemOverviewResponse, SystemSettingsUpdate

router = APIRouter(prefix="/api/system", tags=["system"])


async def _get_setting(db: AsyncSession, key: str, default: str) -> str:
    row = await db.get(SystemSetting, key)
    return row.value if row else default


async def _set_setting(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(SystemSetting, key)
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))


@router.get("/overview", response_model=SystemOverviewResponse)
async def get_overview(
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    active_outbound = (
        await db.execute(select(OutboundConfig).where(OutboundConfig.is_active == True).limit(1))
    ).scalar_one_or_none()

    total_outbounds = (await db.execute(select(func.count(OutboundConfig.id)))).scalar() or 0
    alive_outbounds = (
        await db.execute(
            select(func.count(OutboundConfig.id)).where(OutboundConfig.is_alive == True)
        )
    ).scalar() or 0

    total_clients = (await db.execute(select(func.count(ClientAccount.id)))).scalar() or 0
    active_clients = (
        await db.execute(select(func.count(ClientAccount.id)).where(ClientAccount.enabled == True))
    ).scalar() or 0

    traffic_sum = (
        await db.execute(
            select(
                func.coalesce(func.sum(ClientAccount.upload_bytes), 0)
                + func.coalesce(func.sum(ClientAccount.download_bytes), 0)
            )
        )
    ).scalar() or 0

    interval_min = int(
        await _get_setting(db, "speedtest_interval_min", str(settings.DEFAULT_SPEEDTEST_INTERVAL_MIN))
    )
    run_dl = (await _get_setting(db, "run_speed_dl_on_schedule", "false")) == "true"
    public_host = await _get_setting(db, "public_host", settings.PUBLIC_HOST)
    vless_tcp_port = int(await _get_setting(db, "vless_tcp_port", str(settings.VLESS_TCP_PORT)))
    vless_ws_port = int(await _get_setting(db, "vless_ws_port", str(settings.VLESS_WS_PORT)))
    vless_ws_path = await _get_setting(db, "vless_ws_path", settings.VLESS_WS_PATH)
    trojan_port = int(await _get_setting(db, "trojan_port", str(settings.TROJAN_PORT)))

    return SystemOverviewResponse(
        xray_main_running=xray_manager.is_main_running,
        xray_outbound_running=xray_manager.is_outbound_running,
        active_outbound=active_outbound,
        total_outbounds=total_outbounds,
        alive_outbounds=alive_outbounds,
        total_clients=total_clients,
        active_clients=active_clients,
        total_traffic_bytes=int(traffic_sum),
        speedtest_interval_min=interval_min,
        run_speed_dl_on_schedule=run_dl,
        public_host=public_host,
        vless_tcp_port=vless_tcp_port,
        vless_ws_port=vless_ws_port,
        vless_ws_path=vless_ws_path,
        trojan_port=trojan_port,
        last_speedtest_at=router_scheduler.last_speedtest_at,
        last_watchdog_at=router_scheduler.last_watchdog_at,
        is_testing_now=router_scheduler.is_testing_now,
    )


@router.patch("/settings")
async def update_settings(
    payload: SystemSettingsUpdate,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    reload_needed = False

    if payload.speedtest_interval_min is not None:
        await _set_setting(db, "speedtest_interval_min", str(payload.speedtest_interval_min))
        await router_scheduler.reschedule_speedtest(payload.speedtest_interval_min)

    if payload.run_speed_dl_on_schedule is not None:
        await _set_setting(
            db, "run_speed_dl_on_schedule", "true" if payload.run_speed_dl_on_schedule else "false"
        )

    if payload.public_host is not None:
        await _set_setting(db, "public_host", payload.public_host.strip())

    if payload.vless_tcp_port is not None:
        await _set_setting(db, "vless_tcp_port", str(payload.vless_tcp_port))
        reload_needed = True

    if payload.vless_ws_port is not None:
        await _set_setting(db, "vless_ws_port", str(payload.vless_ws_port))
        reload_needed = True

    if payload.vless_ws_path is not None:
        path = payload.vless_ws_path.strip()
        if not path.startswith("/"):
            path = f"/{path}"
        await _set_setting(db, "vless_ws_path", path)
        reload_needed = True

    if payload.trojan_port is not None:
        await _set_setting(db, "trojan_port", str(payload.trojan_port))
        reload_needed = True

    await db.commit()
    if reload_needed:
        await xray_manager.start_or_reload_main()

    return {"status": "updated", "xray_reloaded": reload_needed}


@router.post("/restart-xray")
async def restart_xray(_: AdminUser = Depends(get_current_admin)):
    await xray_manager.start_or_reload_main()
    await xray_manager.restore_active_outbound_on_boot()
    return {"status": "restarted"}
