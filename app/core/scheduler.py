import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.core.geo_manager import download_dat_file
from app.core.speed_tester import run_concurrent_speed_tests, test_socks_endpoint
from app.core.xray_manager import xray_manager
from app.database import AsyncSessionLocal
from app.models import ClientAccount, LatencyLog, OutboundConfig, SystemSetting
from app.xray.grpc_client import XrayGrpcClient

logger = logging.getLogger(__name__)


class RouterScheduler:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.grpc = XrayGrpcClient(settings.XRAY_GRPC_ADDR)
        self._speedtest_lock = asyncio.Lock()
        self.is_testing_now: bool = False
        self.last_speedtest_at: Optional[str] = None
        self.last_watchdog_at: Optional[str] = None

    async def start(self) -> None:
        interval_min = await self.get_speedtest_interval_min()
        self.scheduler.add_job(
            self.run_speedtest_cycle,
            "interval",
            minutes=interval_min,
            id="speedtest_job",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self.run_watchdog,
            "interval",
            seconds=settings.WATCHDOG_INTERVAL_SEC,
            id="watchdog_job",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self.poll_stats_and_enforce_quotas,
            "interval",
            seconds=settings.STATS_POLL_INTERVAL_SEC,
            id="stats_poll_job",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self.run_weekly_geo_update,
            "interval",
            days=7,
            id="geo_weekly_job",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info(
            "Scheduler started (speedtest=%dm, watchdog=%ds, stats=%ds)",
            interval_min,
            settings.WATCHDOG_INTERVAL_SEC,
            settings.STATS_POLL_INTERVAL_SEC,
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def get_speedtest_interval_min(self) -> int:
        async with AsyncSessionLocal() as db:
            row = await db.get(SystemSetting, "speedtest_interval_min")
            if row and row.value.isdigit():
                return max(1, int(row.value))
        return settings.DEFAULT_SPEEDTEST_INTERVAL_MIN

    async def reschedule_speedtest(self, interval_min: int) -> None:
        self.scheduler.reschedule_job(
            "speedtest_job",
            trigger="interval",
            minutes=max(1, interval_min),
        )
        logger.info("Rescheduled speedtest interval to %d minutes", interval_min)

    async def run_speedtest_cycle(self, run_speed_dl: Optional[bool] = None) -> dict:
        if self._speedtest_lock.locked():
            return {"status": "already_running"}

        async with self._speedtest_lock:
            self.is_testing_now = True
            try:
                async with AsyncSessionLocal() as db:
                    if run_speed_dl is None:
                        dl_row = await db.get(SystemSetting, "run_speed_dl_on_schedule")
                        run_speed_dl = (dl_row.value == "true") if dl_row else False

                    outbounds = (
                        await db.execute(
                            select(OutboundConfig).where(OutboundConfig.enabled == True)
                        )
                    ).scalars().all()

                if not outbounds:
                    self.last_speedtest_at = datetime.now(timezone.utc).isoformat()
                    return {"tested": 0, "alive": 0, "switched_to": None}

                candidates = [(o.id, json.loads(o.xray_outbound_json)) for o in outbounds]
                results = await run_concurrent_speed_tests(candidates, run_speed_dl=bool(run_speed_dl))
                now = datetime.now(timezone.utc)

                alive_sorted = sorted(
                    [r for r in results if r.is_alive and r.latency_ms is not None],
                    key=lambda r: r.latency_ms,
                )

                async with AsyncSessionLocal() as db:
                    for res in results:
                        row = await db.get(OutboundConfig, res.outbound_id)
                        if row:
                            row.is_alive = res.is_alive
                            row.latency_ms = res.latency_ms
                            if res.speed_mbps is not None:
                                row.speed_mbps = res.speed_mbps
                            row.last_error = res.error
                            row.last_tested_at = now
                        db.add(
                            LatencyLog(
                                outbound_id=res.outbound_id,
                                latency_ms=res.latency_ms,
                                speed_mbps=res.speed_mbps,
                                is_alive=res.is_alive,
                                error_message=res.error,
                                tested_at=now,
                            )
                        )
                    await db.commit()

                switched_to: Optional[int] = None
                if alive_sorted:
                    fastest = alive_sorted[0]
                    async with AsyncSessionLocal() as db:
                        best_row = await db.get(OutboundConfig, fastest.outbound_id)
                    if best_row:
                        ok = await xray_manager.switch_outbound(
                            best_row.id, json.loads(best_row.xray_outbound_json)
                        )
                        if ok:
                            switched_to = best_row.id

                self.last_speedtest_at = now.isoformat()
                logger.info(
                    "Speed test finished: %d tested, %d alive, active=%s",
                    len(results),
                    len(alive_sorted),
                    switched_to,
                )
                return {
                    "tested": len(results),
                    "alive": len(alive_sorted),
                    "switched_to": switched_to,
                }
            finally:
                self.is_testing_now = False

    async def run_watchdog(self) -> None:
        self.last_watchdog_at = datetime.now(timezone.utc).isoformat()
        if self._speedtest_lock.locked():
            return

        healthy = await xray_manager.check_active_outbound_health()
        if healthy:
            return

        logger.warning("Watchdog detected active outbound failure! Initiating failover...")
        async with AsyncSessionLocal() as db:
            current_active = (
                await db.execute(
                    select(OutboundConfig).where(OutboundConfig.is_active == True).limit(1)
                )
            ).scalar_one_or_none()
            if current_active:
                current_active.is_alive = False
                current_active.is_active = False
                current_active.last_error = "Watchdog health check failed"
                await db.commit()

            candidates = (
                await db.execute(
                    select(OutboundConfig)
                    .where(
                        OutboundConfig.enabled == True,
                        OutboundConfig.id != (current_active.id if current_active else -1),
                    )
                    .order_by(OutboundConfig.is_alive.desc(), OutboundConfig.latency_ms.asc().nullslast())
                )
            ).scalars().all()

        for cand in candidates:
            ok = await xray_manager.switch_outbound(cand.id, json.loads(cand.xray_outbound_json))
            if ok and xray_manager.active_outbound_port:
                alive, lat, _, _ = await test_socks_endpoint(
                    xray_manager.active_outbound_port, timeout_sec=5.0
                )
                if alive:
                    async with AsyncSessionLocal() as db:
                        row = await db.get(OutboundConfig, cand.id)
                        if row:
                            row.is_alive = True
                            row.latency_ms = lat
                            row.last_error = None
                            await db.commit()
                    logger.info("Watchdog failover succeeded -> outbound #%d (%s)", cand.id, cand.name)
                    return

        logger.error("Watchdog failover found no healthy fallback outbound")

    async def poll_stats_and_enforce_quotas(self) -> None:
        if not xray_manager.is_main_running:
            return
        try:
            stats = await self.grpc.query_user_stats(reset=True)
        except Exception as exc:
            logger.debug("gRPC QueryStats skipped/failed: %s", exc)
            return

        now = datetime.now(timezone.utc)
        to_revoke: list[str] = []

        async with AsyncSessionLocal() as db:
            clients = (await db.execute(select(ClientAccount))).scalars().all()
            for client in clients:
                if client.email in stats:
                    up, down = stats[client.email]
                    client.upload_bytes = (client.upload_bytes or 0) + up
                    client.download_bytes = (client.download_bytes or 0) + down

                if not client.enabled:
                    continue

                reason: Optional[str] = None
                if client.quota_bytes > 0 and client.total_used_bytes >= client.quota_bytes:
                    reason = "quota_exceeded"
                elif client.expires_at is not None:
                    exp = client.expires_at
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if now >= exp:
                        reason = "expired"

                if reason:
                    client.enabled = False
                    client.revoked_reason = reason
                    to_revoke.append(client.email)

            await db.commit()

        for email in to_revoke:
            logger.info("Revoking client %s from Xray via gRPC HandlerService", email)
            await self.grpc.sync_client_remove(email)

    async def run_weekly_geo_update(self) -> None:
        async with AsyncSessionLocal() as db:
            auto_row = await db.get(SystemSetting, "geo_weekly_auto_update")
            if not auto_row or auto_row.value != "true":
                return
            geoip_row = await db.get(SystemSetting, "geoip_url")
            geosite_row = await db.get(SystemSetting, "geosite_url")
            geoip_url = geoip_row.value if geoip_row else settings.GEOIP_URL
            geosite_url = geosite_row.value if geosite_row else settings.GEOSITE_URL

        try:
            await download_dat_file(geoip_url, "geoip.dat")
            await download_dat_file(geosite_url, "geosite.dat")
            await xray_manager.start_or_reload_main()
            logger.info("Weekly scheduled GeoIP/Geosite update completed")
        except Exception as exc:
            logger.error("Weekly scheduled GeoIP/Geosite update failed: %s", exc)


router_scheduler = RouterScheduler()
