from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
from sqlalchemy import select

from app.api import auth, clients, geo, outbounds, routing, system
from app.config import settings
from app.core.activity_tracker import activity_tracker
from app.core.scheduler import router_scheduler
from app.core.xray_manager import xray_manager
from app.database import AsyncSessionLocal, init_db
from app.models import AdminUser, SystemSetting
from app.security import hash_password

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _detect_public_ip() -> str:
    for url in ("https://api.ipify.org", "https://icanhazip.com"):
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(url)
                if r.status_code == 200 and r.text.strip():
                    return r.text.strip()
        except Exception:
            continue
    return ""


async def _bootstrap_defaults() -> None:
    async with AsyncSessionLocal() as db:
        admin = (
            await db.execute(select(AdminUser).limit(1))
        ).scalar_one_or_none()
        if not admin:
            db.add(
                AdminUser(
                    username=settings.DEFAULT_ADMIN_USER,
                    password_hash=hash_password(settings.DEFAULT_ADMIN_PASS),
                )
            )
            logger.info("Bootstrapped default admin user: %s", settings.DEFAULT_ADMIN_USER)

        pub_row = await db.get(SystemSetting, "public_host")
        if not pub_row:
            pub_val = settings.PUBLIC_HOST.strip() or await _detect_public_ip()
            db.add(SystemSetting(key="public_host", value=pub_val))

        defaults = {
            "speedtest_interval_min": str(settings.DEFAULT_SPEEDTEST_INTERVAL_MIN),
            "run_speed_dl_on_schedule": "false",
            "geo_weekly_auto_update": "true",
            "geoip_url": settings.GEOIP_URL,
            "geosite_url": settings.GEOSITE_URL,
            "vless_tcp_port": str(settings.VLESS_TCP_PORT),
            "vless_ws_port": str(settings.VLESS_WS_PORT),
            "vless_ws_path": settings.VLESS_WS_PATH,
            "trojan_port": str(settings.TROJAN_PORT),
        }
        for k, v in defaults.items():
            if not await db.get(SystemSetting, k):
                db.add(SystemSetting(key=k, value=v))
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _bootstrap_defaults()

    await xray_manager.start_bridge()
    try:
        await xray_manager.start_or_reload_main()
        await xray_manager.restore_active_outbound_on_boot()
    except Exception as exc:
        logger.warning("Xray startup warning (check xray binary/config): %s", exc)

    await router_scheduler.start()
    activity_tracker.start()
    yield
    await activity_tracker.stop()
    router_scheduler.shutdown()
    await xray_manager.shutdown()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.include_router(auth.router)
app.include_router(outbounds.router)
app.include_router(clients.router)
app.include_router(clients.sub_router)
app.include_router(routing.router)
app.include_router(geo.router)
app.include_router(system.router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
