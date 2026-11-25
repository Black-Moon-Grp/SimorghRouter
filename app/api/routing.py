from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.core.xray_manager import xray_manager
from app.database import get_db
from app.models import AdminUser, RoutingRule
from app.schemas import RoutingRuleCreate, RoutingRuleResponse

router = APIRouter(prefix="/api/routing", tags=["routing"])


@router.get("", response_model=list[RoutingRuleResponse])
async def list_rules(
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(RoutingRule).order_by(RoutingRule.id.desc()))).scalars().all()
    return rows


@router.post("", response_model=RoutingRuleResponse)
async def create_rule(
    payload: RoutingRuleCreate,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    rule = RoutingRule(
        rule_type=payload.rule_type,
        value=payload.value,
        action=payload.action,
        description=payload.description,
        enabled=payload.enabled,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await xray_manager.start_or_reload_main()
    return rule


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: int,
    _: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    rule = await db.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    await db.delete(rule)
    await db.commit()
    await xray_manager.start_or_reload_main()
    return {"status": "deleted"}


@router.post("/apply")
async def apply_routing(_: AdminUser = Depends(get_current_admin)):
    await xray_manager.start_or_reload_main()
    return {"status": "applied"}
