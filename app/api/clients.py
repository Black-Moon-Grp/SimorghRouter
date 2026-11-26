import base64
from datetime import datetime, timedelta, timezone
import io
import secrets
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
import qrcode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.config import settings
from app.core.activity_tracker import activity_tracker
from app.core.link_parser import generate_client_links
from app.core.xray_manager import xray_manager
from app.database import get_db
from app.models import AdminUser, ClientAccount, ClientActivityLog, SystemSetting
from app.schemas import (
    ClientActivityLogResponse,
    ClientCreate,
    ClientLinksResponse,
    ClientLiveStatusResponse,
    ClientResponse,
    ClientUpdate,
)
from app.xray.grpc_client import XrayGrpcClient

router = APIRouter(prefix="/api/clients", tags=["clients"])
sub_router = APIRouter(tags=["subscription"])
grpc_client = XrayGrpcClient(settings.XRAY_GRPC_ADDR)


async def _get_public_settings(db: AsyncSession) -> tuple[str, int, int, str, int]:
    async def get_val(k: str, default: str) -> str:
        r = await db.get(SystemSetting, k)
        return r.value if r else default

    host = await get_val("public_host", settings.PUBLIC_HOST)
    tcp_port = int(await get_val("vless_tcp_port", str(settings.VLESS_TCP_PORT)))
    ws_port = int(await get_val("vless_ws_port", str(settings.VLESS_WS_PORT)))
    ws_path = await get_val("vless_ws_path", settings.VLESS_WS_PATH)
    trojan_port = int(await get_val("trojan_port", str(settings.TROJAN_PORT)))
    return host, tcp_port, ws_port, ws_path, trojan_port


@router.get("", response_model=list[ClientResponse])
async def list_clients(
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(ClientAccount).order_by(ClientAccount.id.desc()))).scalars().all()
    return rows


@router.post("", response_model=ClientResponse)
async def create_client(
    payload: ClientCreate,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(select(ClientAccount).where(ClientAccount.username == payload.username))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    expires_at = payload.expires_at
    if expires_at is None and payload.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)

    quota_bytes = int(payload.quota_gb * (1024 ** 3)) if payload.quota_gb > 0 else 0
    client = ClientAccount(
        username=payload.username,
        email=f"{payload.username}@v2ray.local",
        uuid=str(uuid.uuid4()),
        trojan_password=secrets.token_urlsafe(16),
        enabled=payload.enabled,
        quota_bytes=quota_bytes,
        expires_at=expires_at,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)

    if client.enabled and xray_manager.is_main_running:
        await grpc_client.sync_client_add(client.email, client.uuid, client.trojan_password)

    return client


@router.patch("/{client_id}", response_model=ClientResponse)
async def update_client(
    client_id: int,
    payload: ClientUpdate,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    client = await db.get(ClientAccount, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if payload.quota_gb is not None:
        client.quota_bytes = int(payload.quota_gb * (1024 ** 3)) if payload.quota_gb > 0 else 0
    if payload.clear_expiration:
        client.expires_at = None
    elif payload.expires_at is not None:
        client.expires_at = payload.expires_at
    if payload.reset_traffic:
        client.upload_bytes = 0
        client.download_bytes = 0
        client.revoked_reason = None
    if payload.enabled is not None:
        client.enabled = payload.enabled
        if payload.enabled:
            client.revoked_reason = None

    await db.commit()
    await db.refresh(client)

    if xray_manager.is_main_running:
        if client.enabled:
            await grpc_client.sync_client_add(client.email, client.uuid, client.trojan_password)
        else:
            await grpc_client.sync_client_remove(client.email)

    return client


@router.delete("/{client_id}")
async def delete_client(
    client_id: int,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    client = await db.get(ClientAccount, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    email = client.email
    await db.delete(client)
    await db.commit()

    if xray_manager.is_main_running:
        await grpc_client.sync_client_remove(email)

    return {"status": "deleted"}


@router.get("/live-status", response_model=list[ClientLiveStatusResponse])
async def get_clients_live_status(
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    clients = (await db.execute(select(ClientAccount).order_by(ClientAccount.id.desc()))).scalars().all()
    results: list[ClientLiveStatusResponse] = []
    for c in clients:
        state = activity_tracker.get_user_state(c.email)
        online = activity_tracker.is_user_online(c.email)
        remaining = max(0, c.quota_bytes - c.total_used_bytes) if c.quota_bytes > 0 else None
        results.append(
            ClientLiveStatusResponse(
                client_id=c.id,
                username=c.username,
                enabled=c.enabled,
                revoked_reason=c.revoked_reason,
                quota_bytes=c.quota_bytes,
                total_used_bytes=c.total_used_bytes,
                remaining_bytes=remaining,
                expires_at=c.expires_at,
                is_online=online,
                last_seen_at=state.last_seen_at if state else None,
                source_ip=state.source_ip if state else None,
                current_destination=state.current_destination if state else None,
                current_outbound=state.current_outbound if state else None,
                current_inbound=state.current_inbound if state else None,
                recent_sites=list(state.recent_sites) if state else [],
            )
        )
    return results


@router.get("/activity-logs", response_model=list[ClientActivityLogResponse])
async def get_activity_logs(
    username: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=150, ge=1, le=500),
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ClientActivityLog).order_by(ClientActivityLog.id.desc()).limit(limit)
    if username:
        stmt = stmt.where(ClientActivityLog.username == username.strip())
    if search:
        stmt = stmt.where(ClientActivityLog.destination.ilike(f"%{search.strip()}%"))
    rows = (await db.execute(stmt)).scalars().all()
    return rows


@router.get("/{client_id}/links", response_model=ClientLinksResponse)
async def get_client_links(
    client_id: int,
    request: Request,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    client = await db.get(ClientAccount, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    host, tcp_port, ws_port, ws_path, trojan_port = await _get_public_settings(db)
    links = generate_client_links(
        username=client.username,
        uuid=client.uuid,
        trojan_password=client.trojan_password,
        public_host=host,
        vless_tcp_port=tcp_port,
        vless_ws_port=ws_port,
        vless_ws_path=ws_path,
        trojan_port=trojan_port,
    )
    base_url = str(request.base_url).rstrip("/")
    sub_url = f"{base_url}/sub/{client.uuid}"
    return ClientLinksResponse(username=client.username, public_host=host, subscription_url=sub_url, **links)


@router.get("/{client_id}/qr")
async def get_client_qr(
    client_id: int,
    request: Request,
    proto: str = Query(default="vless_tcp", pattern="^(vless_tcp|vless_ws|trojan|sub)$"),
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    client = await db.get(ClientAccount, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if proto == "sub":
        target = f"{str(request.base_url).rstrip('/')}/sub/{client.uuid}"
    else:
        host, tcp_port, ws_port, ws_path, trojan_port = await _get_public_settings(db)
        links = generate_client_links(
            username=client.username,
            uuid=client.uuid,
            trojan_password=client.trojan_password,
            public_host=host,
            vless_tcp_port=tcp_port,
            vless_ws_port=ws_port,
            vless_ws_path=ws_path,
            trojan_port=trojan_port,
        )
        target = links[proto]

    img = qrcode.make(target)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@sub_router.get("/sub/{client_uuid}")
async def client_subscription(
    client_uuid: str,
    db: AsyncSession = Depends(get_db),
):
    client = (
        await db.execute(select(ClientAccount).where(ClientAccount.uuid == client_uuid))
    ).scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Subscription not found")

    host, tcp_port, ws_port, ws_path, trojan_port = await _get_public_settings(db)
    links = generate_client_links(
        username=client.username,
        uuid=client.uuid,
        trojan_password=client.trojan_password,
        public_host=host,
        vless_tcp_port=tcp_port,
        vless_ws_port=ws_port,
        vless_ws_path=ws_path,
        trojan_port=trojan_port,
    )
    payload_text = "\n".join([links["vless_tcp"], links["vless_ws"], links["trojan"]])
    b64 = base64.b64encode(payload_text.encode("utf-8")).decode("ascii")

    expire_ts = int(client.expires_at.timestamp()) if client.expires_at else 0
    userinfo = (
        f"upload={client.upload_bytes}; download={client.download_bytes}; "
        f"total={client.quota_bytes}; expire={expire_ts}"
    )
    return PlainTextResponse(
        content=b64,
        headers={
            "Profile-Title": f"SimorghRouter - {client.username}",
            "Subscription-Userinfo": userinfo,
            "Profile-Update-Interval": "1",
        },
    )
