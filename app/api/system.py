import asyncio
import logging
from pathlib import Path
import shutil
import sys
import tempfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.config import settings
from app.core.scheduler import router_scheduler
from app.core.xray_manager import xray_manager
from app.database import get_db
from app.models import AdminUser, ClientAccount, OutboundConfig, SystemSetting
from app.schemas import SystemOverviewResponse, SystemSettingsUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])
APP_ROOT = Path(__file__).resolve().parent.parent.parent


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


@router.get("/update-check")
async def check_github_update(_: AdminUser = Depends(get_current_admin)):
    version_file = settings.data_path / "app_version.txt"
    local_commit = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else "unknown"
    remote_commit = None
    remote_message = None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                settings.GITHUB_API_COMMIT_URL,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "SimorghRouter"},
            )
            if resp.status_code == 200:
                data = resp.json()
                remote_commit = str(data.get("sha", ""))[:7]
                remote_message = (data.get("commit", {}).get("message", "") or "").splitlines()[0]
    except Exception as exc:
        logger.debug("GitHub update check failed: %s", exc)

    return {
        "local_commit": local_commit,
        "remote_commit": remote_commit,
        "remote_message": remote_message,
        "update_available": bool(remote_commit and remote_commit != local_commit),
    }


async def _delayed_service_restart() -> None:
    await asyncio.sleep(1.5)
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "restart", "v2ray-router.service",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception:
        pass


@router.post("/self-update")
async def self_update_from_github(
    background_tasks: BackgroundTasks,
    _: AdminUser = Depends(get_current_admin),
):
    tmp_dir = Path(tempfile.mkdtemp(prefix="simorgh_upd_"))
    try:
        clone_proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", settings.GITHUB_REPO_URL, str(tmp_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(clone_proc.communicate(), timeout=60.0)
        if clone_proc.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"Git clone failed: {stderr.decode('utf-8', errors='replace')[:240]}",
            )

        rev_proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(tmp_dir), "rev-parse", "--short", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rev_out, _ = await rev_proc.communicate()
        new_commit = rev_out.decode("utf-8", errors="replace").strip() or "latest"

        src_app = tmp_dir / "app"
        dst_app = APP_ROOT / "app"
        if not src_app.is_dir():
            raise HTTPException(status_code=500, detail="Downloaded repository missing app/ directory")

        shutil.copytree(src_app, dst_app, dirs_exist_ok=True)
        src_req = tmp_dir / "requirements.txt"
        if src_req.is_file():
            shutil.copy2(src_req, APP_ROOT / "requirements.txt")
            pip_proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "-q", "-r", str(APP_ROOT / "requirements.txt"),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(pip_proc.communicate(), timeout=120.0)

        (settings.data_path / "app_version.txt").write_text(new_commit, encoding="utf-8")
        background_tasks.add_task(_delayed_service_restart)
        return {"status": "updating", "commit": new_commit}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
