import asyncio
import json
import logging
import os
from typing import Any, Optional

from sqlalchemy import select, update

from app.config import settings
from app.core.routing_generator import (
    generate_main_xray_config,
    generate_outbound_runner_config,
    write_json_config,
)
from app.core.speed_tester import _wait_for_port, test_socks_endpoint
from app.database import AsyncSessionLocal
from app.models import ClientAccount, OutboundConfig, RoutingRule, SystemSetting

logger = logging.getLogger(__name__)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


class XrayManager:
    """
    Manages:
    1. Main Xray-core process (inbounds, routing, Iran bypass, gRPC API)
    2. Localhost TCP Bridge (127.0.0.1:10808 -> active blue-green outbound port)
    3. Blue-Green Outbound Xray-core process (10809 / 10810) for zero-drop outbound switching
    """

    def __init__(self) -> None:
        self._main_proc: Optional[asyncio.subprocess.Process] = None
        self._outbound_proc: Optional[asyncio.subprocess.Process] = None
        self._bridge_server: Optional[asyncio.AbstractServer] = None
        self._bridge_target_port: Optional[int] = None
        self._active_outbound_port: Optional[int] = None
        self._lock = asyncio.Lock()

    @property
    def is_main_running(self) -> bool:
        return self._main_proc is not None and self._main_proc.returncode is None

    @property
    def is_outbound_running(self) -> bool:
        return self._outbound_proc is not None and self._outbound_proc.returncode is None

    @property
    def active_outbound_port(self) -> Optional[int]:
        return self._active_outbound_port

    async def start_bridge(self) -> None:
        if self._bridge_server is not None:
            return

        async def handle_client(
            client_reader: asyncio.StreamReader,
            client_writer: asyncio.StreamWriter,
        ) -> None:
            target_port = self._bridge_target_port
            if not target_port:
                client_writer.close()
                return
            try:
                remote_reader, remote_writer = await asyncio.open_connection(
                    "127.0.0.1", target_port
                )
            except Exception:
                client_writer.close()
                return

            await asyncio.gather(
                _pipe(client_reader, remote_writer),
                _pipe(remote_reader, client_writer),
                return_exceptions=True,
            )

        self._bridge_server = await asyncio.start_server(
            handle_client, "127.0.0.1", settings.OUTBOUND_BRIDGE_PORT
        )
        logger.info("Outbound TCP bridge listening on 127.0.0.1:%d", settings.OUTBOUND_BRIDGE_PORT)

    async def stop_bridge(self) -> None:
        if self._bridge_server is not None:
            self._bridge_server.close()
            await self._bridge_server.wait_closed()
            self._bridge_server = None

    async def _load_setting_int(self, db, key: str, default: int) -> int:
        row = await db.get(SystemSetting, key)
        if row and row.value.isdigit():
            return int(row.value)
        return default

    async def _load_setting_str(self, db, key: str, default: str) -> str:
        row = await db.get(SystemSetting, key)
        return row.value if row else default

    async def start_or_reload_main(self) -> None:
        """Generates xray_main.json from SQLite and starts (or restarts) Main Xray-core."""
        async with self._lock:
            async with AsyncSessionLocal() as db:
                clients = (await db.execute(select(ClientAccount))).scalars().all()
                rules = (await db.execute(select(RoutingRule))).scalars().all()
                vless_tcp_port = await _get_int_setting(db, "vless_tcp_port", settings.VLESS_TCP_PORT)
                vless_ws_port = await _get_int_setting(db, "vless_ws_port", settings.VLESS_WS_PORT)
                vless_ws_path = await _get_str_setting(db, "vless_ws_path", settings.VLESS_WS_PATH)
                trojan_port = await _get_int_setting(db, "trojan_port", settings.TROJAN_PORT)

            cfg = generate_main_xray_config(
                clients=list(clients),
                custom_rules=list(rules),
                vless_tcp_port=vless_tcp_port,
                vless_ws_port=vless_ws_port,
                vless_ws_path=vless_ws_path,
                trojan_port=trojan_port,
            )
            write_json_config(settings.xray_main_config_path, cfg)

            await self._stop_process(self._main_proc)
            env = os.environ.copy()
            env["XRAY_LOCATION_ASSET"] = settings.XRAY_ASSETS_DIR
            self._main_proc = await asyncio.create_subprocess_exec(
                settings.XRAY_BIN,
                "run",
                "-c",
                str(settings.xray_main_config_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.sleep(0.4)
            if self._main_proc.returncode is not None:
                err = ""
                if self._main_proc.stderr:
                    err = (await self._main_proc.stderr.read()).decode("utf-8", errors="replace")
                logger.error("Main Xray-core failed to start: %s", err)
                raise RuntimeError(f"Main Xray-core failed to start: {err[:300]}")
            logger.info("Main Xray-core started (PID %d)", self._main_proc.pid)

    async def switch_outbound(self, outbound_id: int, outbound_dict: dict[str, Any]) -> bool:
        """
        Blue-Green outbound switch:
        Starts the new outbound Xray instance on the standby port, verifies it opens,
        atomically flips the local TCP bridge target port, then stops the old outbound Xray.
        Main Xray-core is NEVER restarted.
        """
        async with self._lock:
            next_port = (
                settings.OUTBOUND_PORT_B
                if self._active_outbound_port == settings.OUTBOUND_PORT_A
                else settings.OUTBOUND_PORT_A
            )
            cfg = generate_outbound_runner_config(next_port, outbound_dict)
            cfg_path = settings.data_path / f"xray_outbound_{next_port}.json"
            write_json_config(cfg_path, cfg)

            env = os.environ.copy()
            env["XRAY_LOCATION_ASSET"] = settings.XRAY_ASSETS_DIR
            new_proc = await asyncio.create_subprocess_exec(
                settings.XRAY_BIN,
                "run",
                "-c",
                str(cfg_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            if not await _wait_for_port(next_port, timeout=3.0):
                err = ""
                if new_proc.returncode is not None and new_proc.stderr:
                    err = (await new_proc.stderr.read()).decode("utf-8", errors="replace")
                await self._stop_process(new_proc)
                logger.error("New outbound #%d failed to bind port %d: %s", outbound_id, next_port, err)
                return False

            # Atomic flip
            old_proc = self._outbound_proc
            self._bridge_target_port = next_port
            self._active_outbound_port = next_port
            self._outbound_proc = new_proc

            # Update DB active flag
            async with AsyncSessionLocal() as db:
                await db.execute(update(OutboundConfig).values(is_active=False))
                await db.execute(
                    update(OutboundConfig)
                    .where(OutboundConfig.id == outbound_id)
                    .values(is_active=True)
                )
                await db.commit()

            # Gracefully stop old outbound process
            if old_proc is not None:
                await self._stop_process(old_proc)

            logger.info("Switched active outbound to #%d on port %d (zero-drop)", outbound_id, next_port)
            return True

    async def check_active_outbound_health(self) -> bool:
        if not self.is_outbound_running or not self._active_outbound_port:
            return False
        alive, _, _, _ = await test_socks_endpoint(
            self._active_outbound_port,
            timeout_sec=float(settings.SPEEDTEST_TIMEOUT_SEC),
            run_speed_dl=False,
        )
        return alive

    async def restore_active_outbound_on_boot(self) -> None:
        async with AsyncSessionLocal() as db:
            active = (
                await db.execute(
                    select(OutboundConfig)
                    .where(OutboundConfig.is_active == True, OutboundConfig.enabled == True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not active:
                active = (
                    await db.execute(
                        select(OutboundConfig)
                        .where(OutboundConfig.is_alive == True, OutboundConfig.enabled == True)
                        .order_by(OutboundConfig.latency_ms.asc())
                        .limit(1)
                    )
                ).scalar_one_or_none()

        if active:
            await self.switch_outbound(active.id, json.loads(active.xray_outbound_json))

    async def shutdown(self) -> None:
        await self._stop_process(self._outbound_proc)
        await self._stop_process(self._main_proc)
        await self.stop_bridge()

    @staticmethod
    async def _stop_process(proc: Optional[asyncio.subprocess.Process]) -> None:
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


async def _get_int_setting(db, key: str, default: int) -> int:
    row = await db.get(SystemSetting, key)
    if row and row.value.isdigit():
        return int(row.value)
    return default


async def _get_str_setting(db, key: str, default: str) -> str:
    row = await db.get(SystemSetting, key)
    return row.value if row else default


xray_manager = XrayManager()
