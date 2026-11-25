import asyncio
import json
import logging
import os
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import settings
from app.core.routing_generator import generate_outbound_runner_config

logger = logging.getLogger(__name__)


@dataclass
class SpeedTestResult:
    outbound_id: int
    is_alive: bool
    latency_ms: Optional[float]
    speed_mbps: Optional[float]
    error: Optional[str]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _wait_for_port(port: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return True
        except OSError:
            await asyncio.sleep(0.1)
    return False


async def test_socks_endpoint(
    socks_port: int,
    timeout_sec: float = 8.0,
    run_speed_dl: bool = False,
) -> tuple[bool, Optional[float], Optional[float], Optional[str]]:
    """
    Tests an already-running local SOCKS5 endpoint against TEST_URL (generate_204).
    Returns (is_alive, latency_ms, speed_mbps, error_message).
    """
    proxy_url = f"socks5://127.0.0.1:{socks_port}"
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url,
            timeout=httpx.Timeout(timeout_sec),
            follow_redirects=False,
        ) as client:
            t0 = time.perf_counter()
            resp = await client.get(settings.TEST_URL)
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if resp.status_code not in (200, 204):
                return False, None, None, f"Unexpected HTTP status {resp.status_code}"

            speed_mbps: Optional[float] = None
            if run_speed_dl:
                try:
                    t_dl0 = time.perf_counter()
                    dl_resp = await client.get(settings.SPEED_DL_URL, timeout=15.0)
                    dl_elapsed = time.perf_counter() - t_dl0
                    if dl_resp.status_code == 200 and dl_elapsed > 0:
                        bits = len(dl_resp.content) * 8
                        speed_mbps = round(bits / dl_elapsed / 1_000_000, 2)
                except Exception as dl_exc:
                    logger.debug("Optional speed download failed on port %d: %s", socks_port, dl_exc)

            return True, latency_ms, speed_mbps, None
    except Exception as exc:
        return False, None, None, str(exc)[:240]


async def test_single_outbound(
    outbound_id: int,
    outbound_dict: dict[str, Any],
    semaphore: asyncio.Semaphore,
    run_speed_dl: bool = False,
) -> SpeedTestResult:
    """Spawns an isolated temporary Xray process on a free localhost SOCKS port and tests it."""
    async with semaphore:
        port = _find_free_port()
        cfg = generate_outbound_runner_config(port, outbound_dict)
        tmp_file = Path(tempfile.gettempdir()) / f"xray_test_{outbound_id}_{port}.json"
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            tmp_file.write_text(json.dumps(cfg), encoding="utf-8")
            env = os.environ.copy()
            env["XRAY_LOCATION_ASSET"] = settings.XRAY_ASSETS_DIR
            proc = await asyncio.create_subprocess_exec(
                settings.XRAY_BIN,
                "run",
                "-c",
                str(tmp_file),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            ready = await _wait_for_port(port, timeout=3.0)
            if not ready:
                err_msg = "Xray test instance failed to open SOCKS port"
                if proc.returncode is not None and proc.stderr:
                    stderr_bytes = await proc.stderr.read()
                    err_msg = stderr_bytes.decode("utf-8", errors="replace").strip()[:240] or err_msg
                return SpeedTestResult(outbound_id, False, None, None, err_msg)

            is_alive, latency_ms, speed_mbps, err = await test_socks_endpoint(
                port,
                timeout_sec=float(settings.SPEEDTEST_TIMEOUT_SEC),
                run_speed_dl=run_speed_dl,
            )
            return SpeedTestResult(outbound_id, is_alive, latency_ms, speed_mbps, err)
        except Exception as exc:
            return SpeedTestResult(outbound_id, False, None, None, str(exc)[:240])
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception:
                    proc.kill()
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass


async def run_concurrent_speed_tests(
    candidates: list[tuple[int, dict[str, Any]]],
    run_speed_dl: bool = False,
) -> list[SpeedTestResult]:
    semaphore = asyncio.Semaphore(settings.SPEEDTEST_WORKERS)
    tasks = [
        test_single_outbound(oid, odict, semaphore, run_speed_dl=run_speed_dl)
        for oid, odict in candidates
    ]
    return await asyncio.gather(*tasks)
