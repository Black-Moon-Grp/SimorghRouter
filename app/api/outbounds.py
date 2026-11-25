import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.core.link_parser import fetch_subscription, parse_links_bulk
from app.core.scheduler import router_scheduler
from app.core.xray_manager import xray_manager
from app.database import get_db
from app.models import AdminUser, OutboundConfig
from app.schemas import (
    BulkImportRequest,
    BulkImportResponse,
    OutboundResponse,
    SpeedTestTriggerRequest,
)

router = APIRouter(prefix="/api/outbounds", tags=["outbounds"])


@router.get("", response_model=list[OutboundResponse])
async def list_outbounds(
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(OutboundConfig).order_by(
                OutboundConfig.is_active.desc(),
                OutboundConfig.is_alive.desc(),
                OutboundConfig.latency_ms.asc().nullslast(),
                OutboundConfig.id.desc(),
            )
        )
    ).scalars().all()
    return rows


@router.post("/import", response_model=BulkImportResponse)
async def import_outbounds(
    payload: BulkImportRequest,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    chunks: list[str] = []
    errors: list[str] = []

    if payload.links_text and payload.links_text.strip():
        chunks.append(payload.links_text.strip())

    sub_url = payload.subscription_url.strip() if payload.subscription_url else None
    if sub_url:
        try:
            sub_body = await fetch_subscription(sub_url)
            chunks.append(sub_body)
        except Exception as exc:
            errors.append(f"Subscription fetch failed: {exc}")

    if not chunks and not errors:
        raise HTTPException(status_code=400, detail="Provide links_text or subscription_url")

    combined = "\n".join(chunks)
    parsed_items, parse_errors = parse_links_bulk(combined)
    errors.extend(parse_errors)

    existing_links = set(
        (await db.execute(select(OutboundConfig.raw_link))).scalars().all()
    )

    imported = 0
    skipped_duplicates = 0
    for item in parsed_items:
        if item.raw_link in existing_links:
            skipped_duplicates += 1
            continue
        existing_links.add(item.raw_link)
        db.add(
            OutboundConfig(
                name=item.name,
                protocol=item.protocol,
                address=item.address,
                port=item.port,
                raw_link=item.raw_link,
                xray_outbound_json=json.dumps(item.xray_outbound),
                subscription_url=sub_url,
            )
        )
        imported += 1

    await db.commit()
    return BulkImportResponse(
        imported=imported,
        skipped_duplicates=skipped_duplicates,
        errors=errors[:50],
    )


@router.post("/test")
async def trigger_speed_test(
    payload: SpeedTestTriggerRequest,
    background_tasks: BackgroundTasks,
    _: AdminUser = Depends(get_current_admin),
):
    if router_scheduler.is_testing_now:
        return {"status": "already_running"}
    background_tasks.add_task(router_scheduler.run_speedtest_cycle, payload.run_speed_dl)
    return {"status": "started"}


@router.post("/{outbound_id}/activate")
async def activate_outbound(
    outbound_id: int,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(OutboundConfig, outbound_id)
    if not row:
        raise HTTPException(status_code=404, detail="Outbound not found")
    ok = await xray_manager.switch_outbound(row.id, json.loads(row.xray_outbound_json))
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to start selected outbound on Xray")
    return {"status": "activated", "outbound_id": row.id}


@router.patch("/{outbound_id}/toggle", response_model=OutboundResponse)
async def toggle_outbound(
    outbound_id: int,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(OutboundConfig, outbound_id)
    if not row:
        raise HTTPException(status_code=404, detail="Outbound not found")
    row.enabled = not row.enabled
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/dead")
async def purge_dead_outbounds(
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        delete(OutboundConfig).where(
            OutboundConfig.is_alive == False,
            OutboundConfig.is_active == False,
            OutboundConfig.last_tested_at.is_not(None),
        )
    )
    await db.commit()
    return {"deleted": res.rowcount or 0}


@router.delete("/{outbound_id}")
async def delete_outbound(
    outbound_id: int,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(OutboundConfig, outbound_id)
    if not row:
        raise HTTPException(status_code=404, detail="Outbound not found")
    if row.is_active:
        raise HTTPException(status_code=400, detail="Cannot delete currently active outbound")
    await db.delete(row)
    await db.commit()
    return {"status": "deleted"}
