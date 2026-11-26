import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import os
import re
from typing import Optional

from sqlalchemy import delete, func, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import ClientAccount, ClientActivityLog

logger = logging.getLogger(__name__)

# Matches Xray access log lines:
# 2026/11/26 21:20:05.123456 from 172.30.10.1:54321 accepted tcp:www.google.com:443 [in-vless-tcp -> proxy] email: test1@v2ray.local
ACCESS_LOG_RE = re.compile(
    r"^\S+\s+\S+\s+from\s+(?:tcp:|udp:)?(?P<src>.+?):\d+\s+accepted\s+"
    r"(?P<net>tcp|udp):(?P<dest>\S+)\s+\[(?P<inbound>[^\s\]]+)\s*->\s*(?P<outbound>[^\s\]]+)\]"
    r"(?:\s+email:\s*(?P<email>\S+))?"
)

ONLINE_THRESHOLD_SEC = 60
MAX_LOG_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_DB_LOG_ROWS = 5000


@dataclass
class LiveUserState:
    last_seen_at: datetime
    source_ip: str
    current_destination: str
    current_outbound: str
    current_inbound: str
    recent_sites: deque[str] = field(default_factory=lambda: deque(maxlen=15))


class ActivityTracker:
    def __init__(self) -> None:
        self.live_users: dict[str, LiveUserState] = {}
        self._task: Optional[asyncio.Task] = None
        self._file_offset: int = 0
        self._running: bool = False
        self._prune_counter: int = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run_loop())
            logger.info("Client activity tracker started")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def is_user_online(self, email: str) -> bool:
        state = self.live_users.get(email)
        if not state:
            return False
        return (datetime.now(timezone.utc) - state.last_seen_at) <= timedelta(
            seconds=ONLINE_THRESHOLD_SEC
        )

    def get_user_state(self, email: str) -> Optional[LiveUserState]:
        return self.live_users.get(email)

    async def _run_loop(self) -> None:
        log_path = settings.data_path / "xray_access.log"
        # Start tailing from current end on boot so we don't replay stale history
        if log_path.is_file():
            self._file_offset = log_path.stat().st_size

        while self._running:
            try:
                await asyncio.sleep(3)
                if not log_path.is_file():
                    self._file_offset = 0
                    continue

                size = log_path.stat().st_size
                if size < self._file_offset:
                    self._file_offset = 0

                if size > self._file_offset:
                    with log_path.open("r", encoding="utf-8", errors="replace") as f:
                        f.seek(self._file_offset)
                        new_lines = f.readlines()
                        self._file_offset = f.tell()

                    if new_lines:
                        await self._process_lines(new_lines)

                if size > MAX_LOG_FILE_BYTES:
                    try:
                        os.truncate(log_path, 0)
                        self._file_offset = 0
                    except OSError:
                        pass

                self._prune_counter += 1
                if self._prune_counter >= 100:
                    self._prune_counter = 0
                    await self._prune_old_logs()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Activity tracker loop error: %s", exc)

    async def _process_lines(self, lines: list[str]) -> None:
        now = datetime.now(timezone.utc)
        parsed_events: list[tuple[str, str, str, str, str, str]] = []

        for raw in lines:
            m = ACCESS_LOG_RE.match(raw.strip())
            if not m:
                continue
            email = m.group("email")
            inbound = m.group("inbound")
            if not email or inbound == "api":
                continue

            src = m.group("src").strip("[]")
            net = m.group("net")
            dest = m.group("dest")
            outbound = m.group("outbound")

            state = self.live_users.get(email)
            if state is None:
                state = LiveUserState(
                    last_seen_at=now,
                    source_ip=src,
                    current_destination=dest,
                    current_outbound=outbound,
                    current_inbound=inbound,
                )
                self.live_users[email] = state
            else:
                state.last_seen_at = now
                state.source_ip = src
                state.current_destination = dest
                state.current_outbound = outbound
                state.current_inbound = inbound

            if not state.recent_sites or state.recent_sites[0] != dest:
                state.recent_sites.appendleft(dest)

            parsed_events.append((email, src, dest, net, inbound, outbound))

        if not parsed_events:
            return

        async with AsyncSessionLocal() as db:
            clients = (await db.execute(select(ClientAccount))).scalars().all()
            by_email = {c.email: c for c in clients}

            for email, src, dest, net, inbound, outbound in parsed_events:
                client = by_email.get(email)
                username = client.username if client else email.split("@", 1)[0]
                db.add(
                    ClientActivityLog(
                        client_id=client.id if client else None,
                        username=username,
                        source_ip=src,
                        destination=dest[:250],
                        network=net,
                        inbound_tag=inbound,
                        outbound_tag=outbound,
                        created_at=now,
                    )
                )
            await db.commit()

    async def _prune_old_logs(self) -> None:
        async with AsyncSessionLocal() as db:
            total = (await db.execute(select(func.count(ClientActivityLog.id)))).scalar() or 0
            if total > MAX_DB_LOG_ROWS:
                cutoff_id = (
                    await db.execute(
                        select(ClientActivityLog.id)
                        .order_by(ClientActivityLog.id.desc())
                        .offset(MAX_DB_LOG_ROWS)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if cutoff_id is not None:
                    await db.execute(
                        delete(ClientActivityLog).where(ClientActivityLog.id <= cutoff_id)
                    )
                    await db.commit()


activity_tracker = ActivityTracker()
