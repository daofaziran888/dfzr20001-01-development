"""试配审评路由：小样领取、评审签结/更正、配方确认回执、审评视图与确认队列。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.blend_review import blend_review_service

router = APIRouter(prefix="/api/blend-review", tags=["试配审评"])


class IssueSamplePayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class SignoffPayload(BaseModel):
    taste_score: float
    comment: str
    aroma: str | None = None
    reviewer: str | None = None


class ConfirmPayload(BaseModel):
    signoff_id: int
    request_id: str | None = None
    operator: str | None = None


@router.get("/queue")
def review_queue(status: str | None = None) -> dict[str, Any]:
    """配方确认队列：未确认配方实时反映签结变化，已确认配方可查历史回执。"""
    return {"ok": True, "items": blend_review_service.review_queue(status)}


@router.get("/formulas/{blend_id}")
def formula_review_view(blend_id: int) -> dict[str, Any]:
    """单张配方的试配审评视图：各小样签结并排呈现，含历次评分与实际办件。"""
    view = blend_review_service.review_view(blend_id)
    if not view.get("found"):
        raise HTTPException(status_code=404, detail=f"拼配配方 {blend_id} 不存在")
    return {"ok": True, "view": view}


@router.get("/samples")
def list_samples(blend_id: int | None = None) -> dict[str, Any]:
    return {"ok": True, "items": blend_review_service.list_samples(blend_id)}


@router.post("/samples")
def issue_sample(payload: IssueSamplePayload) -> dict[str, Any]:
    """领取拼配小样，小样必须绑定一张实际存在的拼配配方。"""
    sample, errors = blend_review_service.issue_sample(payload.values)
    if sample is None:
        raise HTTPException(status_code=400, detail="；".join(errors))
    return {"ok": True, "message": "拼配小样已领取", "sample": sample}


@router.get("/samples/{sample_id}/signoffs")
def signoff_history(sample_id: int) -> dict[str, Any]:
    """返回该小样的全部评审签结版本（含更正前分数）。"""
    if blend_review_service._find_sample(sample_id) is None:
        raise HTTPException(status_code=404, detail=f"拼配小样 {sample_id} 不存在")
    return {"ok": True, "items": blend_review_service.get_signoff_history(sample_id)}


@router.post("/samples/{sample_id}/signoffs")
def submit_signoff(sample_id: int, payload: SignoffPayload) -> dict[str, Any]:
    """登记试配评语；同一小样再次提交即分数更正，旧版本完整保留。"""
    signoff, errors = blend_review_service.submit_signoff(sample_id, payload.model_dump())
    if signoff is None:
        raise HTTPException(status_code=400, detail="；".join(errors))
    action = "更正签结" if signoff.get("is_correction") else "评审签结"
    return {"ok": True, "message": f"{action}已登记（第{signoff['version']}版）", "signoff": signoff}


@router.post("/formulas/{blend_id}/confirm")
def confirm_formula(blend_id: int, payload: ConfirmPayload) -> dict[str, Any]:
    """制茶师凭评审签结确认配方；同签结重交幂等，只回放原回执或要求重新核对。"""
    receipt, message = blend_review_service.confirm_formula(
        blend_id,
        payload.signoff_id,
        request_id=payload.request_id or "",
        operator=payload.operator or "",
    )
    if receipt is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "receipt": receipt}


@router.get("/formulas/{blend_id}/receipts")
def formula_receipts(blend_id: int) -> dict[str, Any]:
    """查询配方已办理的确认回执（含当时签结快照）。"""
    return {"ok": True, "items": blend_review_service.list_receipts(blend_id)}
