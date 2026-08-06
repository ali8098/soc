"""Empty-default stubs for legacy single-tenant API surfaces.

The canonical V1 frontend was lifted from the single-tenant SocTalk
codebase and still calls a handful of routes that haven't been bridged
to the V1 cases/investigation_runs model: ``/api/events/stream`` (SSE),
``/api/review/*``, ``/api/analytics/*``, ``/api/audit/*``,
``/api/settings``. Until those bridges land we return empty/default
shapes so the pages render without an error banner.

Auth: every route here is gated by the same session middleware; an
unauthenticated request gets the layout's pre-login probe handling
(401 → user=null on the SPA).

Side-effecting routes (POST /review/{id}/approve, etc.) intentionally
404 — they're disabled until the real bridge lands.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from soctalk.core.tenancy.auth import current_identity
from soctalk.core.tenancy.decorators import require_permission_any
from soctalk.core.tenancy.permissions import Permission

if TYPE_CHECKING:
    from soctalk.core.tenancy.auth import UserIdentity

_REVIEW_DECIDE_GUARD = require_permission_any(
    (Permission.REVIEW_DECIDE, "mssp"),
    (Permission.TENANT_REVIEW_DECIDE, "tenant"),
)
_REVIEW_ACCESS_GUARD = _REVIEW_DECIDE_GUARD

router = APIRouter(tags=["legacy-stubs"], dependencies=[Depends(current_identity)])

# ---------------------------------------------------------------------------
# /api/events/stream — SSE heartbeat
# ---------------------------------------------------------------------------


@router.get("/api/events/stream")
async def events_stream(request: Request) -> StreamingResponse:
    db = getattr(request.state, "db", None)
    if db is not None:
        try:
            await db.close()
        except Exception:  # noqa: BLE001
            pass

    async def gen():
        yield "event: ping\n" + "data: {}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                await asyncio.sleep(25)
                yield "event: ping\n" + "data: {}\n\n"
        except asyncio.CancelledError:
            return

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


# ---------------------------------------------------------------------------
# /api/review/pending
# ---------------------------------------------------------------------------


class _PendingReviewItem(BaseModel):
    id: str
    investigation_id: str
    tenant_id: str | None = None
    status: str
    title: str
    description: str
    max_severity: str
    alert_count: int
    malicious_count: int = 0
    suspicious_count: int = 0
    clean_count: int = 0
    findings: list[str] = []
    enrichments: dict[str, Any] = {}
    misp_context: dict[str, Any] | None = None
    ai_decision: str | None = None
    ai_confidence: float | None = None
    ai_assessment: str | None = None
    ai_recommendation: str | None = None
    timeout_seconds: int = 3600
    created_at: str
    expires_at: str | None = None


class _PendingReviewList(BaseModel):
    items: list[_PendingReviewItem] = []
    total: int = 0
    page: int = 1
    page_size: int = 50
    has_more: bool = False


@router.get(
    "/api/review/pending",
    response_model=_PendingReviewList,
    dependencies=[Depends(_REVIEW_ACCESS_GUARD)],
)
async def review_pending(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> _PendingReviewList:
    from soctalk.core.tenancy.context import tenant_context
    from soctalk.core.tenancy.db import (
        get_app_sessionmaker,
        get_mssp_sessionmaker,
    )

    identity = current_identity(request)
    offset = (page - 1) * page_size
    count_sql = text(
        "SELECT count(*) FROM pending_reviews WHERE status = 'pending'"
    )
    list_sql = text(
        """
        SELECT id::text, investigation_id::text, tenant_id::text, status, title, description,
               max_severity, alert_count, malicious_count, suspicious_count,
               clean_count, findings, enrichments, misp_context, ai_decision,
               ai_confidence, ai_assessment, ai_recommendation,
               timeout_seconds, created_at, expires_at
        FROM pending_reviews
        WHERE status = 'pending'
        ORDER BY created_at DESC
        OFFSET :off LIMIT :lim
        """
    )

    if identity.role in _MSSP_LEVEL_ROLES:
        sm = get_mssp_sessionmaker()
        async with sm() as s:
            total = (await s.execute(count_sql)).scalar_one()
            rows = (
                await s.execute(list_sql, {"off": offset, "lim": page_size})
            ).mappings().all()
    else:
        tid = _effective_review_tenant(identity)
        if tid is None:
            return _PendingReviewList(
                items=[], total=0, page=page, page_size=page_size, has_more=False
            )
        sm = get_app_sessionmaker()
        async with sm() as s:
            async with tenant_context(s, tid):
                total = (await s.execute(count_sql)).scalar_one()
                rows = (
                    await s.execute(list_sql, {"off": offset, "lim": page_size})
                ).mappings().all()

    items = [_pending_review_item(r) for r in rows]
    return _PendingReviewList(
        items=items,
        total=int(total or 0),
        page=page,
        page_size=page_size,
        has_more=(offset + len(items)) < int(total or 0),
    )


_MSSP_LEVEL_ROLES = {"platform_admin", "mssp_admin", "mssp_manager"}


def _effective_review_tenant(identity: "UserIdentity"):
    return getattr(identity, "current_tenant", None) or identity.tenant_id


def _pending_review_item(r: "Any") -> _PendingReviewItem:
    def _iso(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    return _PendingReviewItem(
        id=r["id"],
        investigation_id=r["investigation_id"],
        tenant_id=r["tenant_id"],
        status=r["status"],
        title=r["title"],
        description=r["description"],
        max_severity=r["max_severity"],
        alert_count=int(r["alert_count"] or 0),
        malicious_count=int(r["malicious_count"] or 0),
        suspicious_count=int(r["suspicious_count"] or 0),
        clean_count=int(r["clean_count"] or 0),
        findings=list(r["findings"] or []),
        enrichments=dict(r["enrichments"] or {}),
        misp_context=(dict(r["misp_context"]) if r["misp_context"] else None),
        ai_decision=r["ai_decision"],
        ai_confidence=(
            float(r["ai_confidence"]) if r["ai_confidence"] is not None else None
        ),
        ai_assessment=r["ai_assessment"],
        ai_recommendation=r["ai_recommendation"],
        timeout_seconds=int(r["timeout_seconds"] or 3600),
        created_at=_iso(r["created_at"]) or "",
        expires_at=_iso(r["expires_at"]),
    )


_REVIEW_COLUMNS = """
    id::text, investigation_id::text, tenant_id::text, status, title, description,
    max_severity, alert_count, malicious_count, suspicious_count,
    clean_count, findings, enrichments, misp_context, ai_decision,
    ai_confidence, ai_assessment, ai_recommendation,
    timeout_seconds, created_at, expires_at
"""


@router.get(
    "/api/review/{review_id}",
    response_model=_PendingReviewItem,
    dependencies=[Depends(_REVIEW_ACCESS_GUARD)],
)
async def review_detail(review_id: str, request: Request) -> _PendingReviewItem:
    from fastapi import HTTPException

    from soctalk.core.tenancy.context import tenant_context
    from soctalk.core.tenancy.db import (
        get_app_sessionmaker,
        get_mssp_sessionmaker,
    )

    identity = current_identity(request)
    sql = text(
        f"SELECT {_REVIEW_COLUMNS} FROM pending_reviews WHERE id = :rid"
    )

    if identity.role in _MSSP_LEVEL_ROLES:
        sm = get_mssp_sessionmaker()
        async with sm() as s:
            row = (await s.execute(sql, {"rid": review_id})).mappings().first()
    else:
        tid = _effective_review_tenant(identity)
        if tid is None:
            raise HTTPException(403, "tenant scope required")
        sm = get_app_sessionmaker()
        async with sm() as s:
            async with tenant_context(s, tid):
                row = (await s.execute(sql, {"rid": review_id})).mappings().first()

    if row is None:
        raise HTTPException(404, "review not found")
    return _pending_review_item(row)


class _ReviewActionResponse(BaseModel):
    success: bool = True
    review_id: str
    new_status: str
    investigation_id: str


async def _resolve_pending_review(
    review_id: str, identity: "UserIdentity"
) -> dict[str, Any]:
    from fastapi import HTTPException

    from soctalk.core.tenancy.context import tenant_context
    from soctalk.core.tenancy.db import (
        get_app_sessionmaker,
        get_mssp_sessionmaker,
    )

    sql = (
        "SELECT id::text, investigation_id::text, tenant_id::text, status, enrichments "
        "FROM pending_reviews WHERE id = :rid"
    )
    if identity.role in _MSSP_LEVEL_ROLES:
        sm = get_mssp_sessionmaker()
        async with sm() as s:
            r = (
                await s.execute(text(sql), {"rid": review_id})
            ).mappings().first()
    else:
        tid = _effective_review_tenant(identity)
        if tid is None:
            raise HTTPException(403, "tenant scope required")
        sm = get_app_sessionmaker()
        async with sm() as s:
            async with tenant_context(s, tid):
                r = (
                    await s.execute(text(sql), {"rid": review_id})
                ).mappings().first()
    if r is None:
        raise HTTPException(404, "review not found")
    return dict(r)


async def _apply_review_decision(
    review_id: str,
    investigation_id: str,
    tenant_id: str | None,
    identity: "UserIdentity",
    decision: str,
    feedback: str | None,
) -> _ReviewActionResponse:
    from uuid import UUID

    from soctalk.core.ir.review_events import (
        _DECISION_TO_REVIEW_STATUS,
        record_human_decision_received,
    )
    from soctalk.core.tenancy.context import tenant_context
    from soctalk.core.tenancy.db import (
        get_app_sessionmaker,
        get_mssp_sessionmaker,
    )

    tenant_uuid = UUID(tenant_id) if tenant_id else None
    if identity.role in _MSSP_LEVEL_ROLES:
        sm = get_mssp_sessionmaker()
        async with sm() as s:
            await record_human_decision_received(
                s,
                review_id=UUID(review_id),
                investigation_id=UUID(investigation_id),
                tenant_id=tenant_uuid,
                decision=decision,
                feedback=feedback,
                reviewer=identity.email,
            )
            await s.commit()
    else:
        sm = get_app_sessionmaker()
        async with sm() as s:
            async with tenant_context(s, tenant_uuid):
                await record_human_decision_received(
                    s,
                    review_id=UUID(review_id),
                    investigation_id=UUID(investigation_id),
                    tenant_id=tenant_uuid,
                    decision=decision,
                    feedback=feedback,
                    reviewer=identity.email,
                )
                await s.commit()
    return _ReviewActionResponse(
        success=True,
        review_id=review_id,
        new_status=_DECISION_TO_REVIEW_STATUS.get(decision, decision),
        investigation_id=investigation_id,
    )


class _ApproveBody(BaseModel):
    feedback: str | None = None


@router.post(
    "/api/review/{review_id}/approve",
    response_model=_ReviewActionResponse,
    dependencies=[Depends(_REVIEW_DECIDE_GUARD)],
)
async def review_approve(
    review_id: str, body: _ApproveBody, request: Request
) -> _ReviewActionResponse:
    from fastapi import HTTPException

    identity = current_identity(request)
    review = await _resolve_pending_review(review_id, identity)
    if review["status"] != "pending":
        raise HTTPException(409, f"review already {review['status']}")
    return await _apply_review_decision(
        review_id, review["investigation_id"], review["tenant_id"], identity,
        "approve", body.feedback,
    )


@router.post(
    "/api/review/{review_id}/reject",
    response_model=_ReviewActionResponse,
    dependencies=[Depends(_REVIEW_DECIDE_GUARD)],
)
async def review_reject(
    review_id: str, body: _ApproveBody, request: Request
) -> _ReviewActionResponse:
    from fastapi import HTTPException

    identity = current_identity(request)
    review = await _resolve_pending_review(review_id, identity)
    if review["status"] != "pending":
        raise HTTPException(409, f"review already {review['status']}")
    return await _apply_review_decision(
        review_id, review["investigation_id"], review["tenant_id"], identity,
        "reject", body.feedback,
    )


class _RequestInfoBody(BaseModel):
    questions: list[str] = []


@router.post(
    "/api/review/{review_id}/request-info",
    response_model=_ReviewActionResponse,
    dependencies=[Depends(_REVIEW_DECIDE_GUARD)],
)
async def review_request_info(
    review_id: str, body: _RequestInfoBody, request: Request
) -> _ReviewActionResponse:
    from fastapi import HTTPException

    identity = current_identity(request)
    review = await _resolve_pending_review(review_id, identity)
    if review["status"] != "pending":
        raise HTTPException(409, f"review already {review['status']}")
    feedback = "Questions: " + " | ".join(body.questions) if body.questions else None
    return await _apply_review_decision(
        review_id, review["investigation_id"], review["tenant_id"], identity,
        "more_info", feedback,
    )


class _ExpireBody(BaseModel):
    reason: str | None = None


@router.post(
    "/api/review/{review_id}/expire",
    response_model=_ReviewActionResponse,
    dependencies=[Depends(_REVIEW_DECIDE_GUARD)],
)
async def review_expire(
    review_id: str, body: _ExpireBody, request: Request
) -> _ReviewActionResponse:
    from uuid import UUID

    from fastapi import HTTPException

    from soctalk.core.ir.review_events import record_human_review_expired
    from soctalk.core.tenancy.context import tenant_context
    from soctalk.core.tenancy.db import (
        get_app_sessionmaker,
        get_mssp_sessionmaker,
    )

    identity = current_identity(request)
    review = await _resolve_pending_review(review_id, identity)
    if review["status"] != "pending":
        raise HTTPException(409, f"review already {review['status']}")

    tenant_uuid = UUID(review["tenant_id"]) if review["tenant_id"] else None
    if tenant_uuid is None:
        raise HTTPException(500, "review missing tenant_id")

    if identity.role in _MSSP_LEVEL_ROLES:
        sm = get_mssp_sessionmaker()
        async with sm() as s:
            await record_human_review_expired(
                s,
                review_id=UUID(review_id),
                investigation_id=UUID(review["investigation_id"]),
                tenant_id=tenant_uuid,
                reason=body.reason,
                reviewer=identity.email,
            )
            await s.commit()
    else:
        sm = get_app_sessionmaker()
        async with sm() as s:
            async with tenant_context(s, tenant_uuid):
                await record_human_review_expired(
                    s,
                    review_id=UUID(review_id),
                    investigation_id=UUID(review["investigation_id"]),
                    tenant_id=tenant_uuid,
                    reason=body.reason,
                    reviewer=identity.email,
                )
                await s.commit()
    return _ReviewActionResponse(
        success=True,
        review_id=review_id,
        new_status="expired",
        investigation_id=review["investigation_id"],
    )


# ---------------------------------------------------------------------------
# /api/analytics/*
# ---------------------------------------------------------------------------


async def _analytics_session_for(identity: "UserIdentity"):
    from soctalk.core.tenancy.context import tenant_context
    from soctalk.core.tenancy.db import get_app_sessionmaker, get_mssp_sessionmaker

    if identity.role in _MSSP_LEVEL_ROLES:
        sm = get_mssp_sessionmaker()
        async with sm() as s:
            yield s
    else:
        sm = get_app_sessionmaker()
        async with sm() as s:
            async with tenant_context(s, identity.tenant_id):
                yield s


async def _kpis(session, days: int) -> dict[str, Any]:
    from sqlalchemy import text as _t

    p = {"d": int(days)}
    inv = (
        await session.execute(
            _t(
                """
                SELECT
                    COUNT(*)::int                                   AS total,
                    COUNT(*) FILTER (WHERE status = 'auto_closed_fp')::int AS auto_closed,
                    COUNT(*) FILTER (WHERE status != 'active' AND closed_at IS NOT NULL)::int AS closed_any,
                    AVG(EXTRACT(EPOCH FROM (closed_at - opened_at)))
                        FILTER (WHERE closed_at IS NOT NULL)        AS mean_decision_s
                FROM investigations
                WHERE created_at >= now() - make_interval(days => :d)
                """
            ),
            p,
        )
    ).mappings().first() or {}
    pr = (
        await session.execute(
            _t(
                """
                SELECT
                    COUNT(*)::int                                       AS total_reviews,
                    COUNT(*) FILTER (WHERE ai_decision = 'escalate')::int AS ai_escalated,
                    AVG(ai_confidence) FILTER (WHERE ai_confidence IS NOT NULL) AS avg_conf,
                    COUNT(*) FILTER (WHERE ai_confidence >= 0.8)::int   AS high_conf,
                    COUNT(*) FILTER (WHERE status = 'rejected')::int    AS overridden
                FROM pending_reviews
                WHERE created_at >= now() - make_interval(days => :d)
                """
            ),
            p,
        )
    ).mappings().first() or {}

    total = int(inv.get("total") or 0)
    total_reviews = int(pr.get("total_reviews") or 0)
    ai_escalated = int(pr.get("ai_escalated") or 0)
    overridden = int(pr.get("overridden") or 0)
    return {
        "auto_close_rate": (int(inv.get("auto_closed") or 0) / total) if total else 0.0,
        "escalation_rate": (ai_escalated / total) if total else 0.0,
        "human_override_rate": (overridden / ai_escalated) if ai_escalated else 0.0,
        "mean_time_to_decision_seconds": (
            float(inv["mean_decision_s"]) if inv.get("mean_decision_s") else None
        ),
        "total_investigations": total,
        "auto_closed_count": int(inv.get("auto_closed") or 0),
        "escalated_count": ai_escalated,
        "human_reviewed_count": total_reviews,
        "avg_ai_confidence": (
            float(pr["avg_conf"]) if pr.get("avg_conf") is not None else None
        ),
        "high_confidence_rate": (
            (int(pr.get("high_conf") or 0) / total_reviews) if total_reviews else 0.0
        ),
    }


async def _ai_behavior(session, days: int) -> dict[str, Any]:
    from sqlalchemy import text as _t

    p = {"d": int(days)}
    rows = (
        await session.execute(
            _t(
                """
                SELECT width_bucket(ai_confidence, 0.0, 1.0001, 10) AS bucket,
                       COUNT(*)::int                                 AS n
                FROM pending_reviews
                WHERE created_at >= now() - make_interval(days => :d)
                  AND ai_confidence IS NOT NULL
                GROUP BY bucket
                ORDER BY bucket
                """
            ),
            p,
        )
    ).all()
    confidence_distribution = [
        {"range_label": f"{(r[0] - 1) / 10:.1f}-{r[0] / 10:.1f}", "count": int(r[1])}
        for r in rows
    ]
    daily = (
        await session.execute(
            _t(
                """
                SELECT date_trunc('day', created_at) AS day,
                       ai_decision,
                       COUNT(*)::int AS n
                FROM pending_reviews
                WHERE created_at >= now() - make_interval(days => :d)
                  AND ai_decision IS NOT NULL
                GROUP BY day, ai_decision
                ORDER BY day
                """
            ),
            p,
        )
    ).all()
    trends_by_day: dict[str, dict[str, Any]] = {}
    for day, decision, n in daily:
        key = day.isoformat() if day else "unknown"
        bucket = trends_by_day.setdefault(
            key,
            {
                "period": key,
                "close": 0,
                "escalate": 0,
                "needs_more_info": 0,
                "suspicious": 0,
            },
        )
        col = (decision or "").replace("-", "_")
        if col in bucket:
            bucket[col] = int(n)
    decision_trends = [trends_by_day[k] for k in sorted(trends_by_day.keys())]
    breakdown = (
        await session.execute(
            _t(
                """
                SELECT max_severity, COUNT(*)::int AS n
                FROM pending_reviews
                WHERE created_at >= now() - make_interval(days => :d)
                  AND ai_decision = 'escalate'
                GROUP BY max_severity
                ORDER BY n DESC
                """
            ),
            p,
        )
    ).all()
    breakdown_total = sum(int(r[1]) for r in breakdown) or 1
    escalation_breakdown = [
        {
            "reason": (r[0] or "Unknown").title(),
            "count": int(r[1]),
            "percentage": int(r[1]) / breakdown_total,
        }
        for r in breakdown
    ]
    avg_by = (
        await session.execute(
            _t(
                """
                SELECT ai_decision, AVG(ai_confidence)::float AS c
                FROM pending_reviews
                WHERE created_at >= now() - make_interval(days => :d)
                  AND ai_confidence IS NOT NULL
                  AND ai_decision IS NOT NULL
                GROUP BY ai_decision
                """
            ),
            p,
        )
    ).all()
    avg_confidence_by_decision = {
        r[0]: float(r[1]) for r in avg_by if r[1] is not None
    }
    return {
        "confidence_distribution": confidence_distribution,
        "decision_trends": decision_trends,
        "escalation_breakdown": escalation_breakdown,
        "avg_confidence_by_decision": avg_confidence_by_decision,
    }


async def _human_review(session, days: int) -> dict[str, Any]:
    from sqlalchemy import text as _t

    p = {"d": int(days)}
    row = (
        await session.execute(
            _t(
                """
                SELECT
                    COUNT(*)::int                                       AS total,
                    COUNT(*) FILTER (WHERE status = 'approved')::int    AS approved,
                    COUNT(*) FILTER (WHERE status = 'rejected')::int    AS rejected,
                    COUNT(*) FILTER (WHERE status = 'info_requested')::int AS info_requested,
                    COUNT(*) FILTER (WHERE status = 'expired')::int     AS expired,
                    COUNT(*) FILTER (WHERE status = 'pending')::int     AS pending,
                    AVG(EXTRACT(EPOCH FROM (responded_at - created_at)))
                        FILTER (WHERE responded_at IS NOT NULL)         AS avg_review_s,
                    COUNT(*) FILTER (
                        WHERE ai_decision = 'escalate' AND status = 'approved'
                    )::int                                              AS ai_agreed,
                    COUNT(*) FILTER (
                        WHERE ai_decision = 'escalate' AND status = 'rejected'
                    )::int                                              AS ai_overridden
                FROM pending_reviews
                WHERE created_at >= now() - make_interval(days => :d)
                """
            ),
            p,
        )
    ).mappings().first() or {}
    total = int(row.get("total") or 0)
    agreed = int(row.get("ai_agreed") or 0)
    overridden = int(row.get("ai_overridden") or 0)
    return {
        "total_reviews": total,
        "approved": int(row.get("approved") or 0),
        "rejected": int(row.get("rejected") or 0),
        "info_requested": int(row.get("info_requested") or 0),
        "expired": int(row.get("expired") or 0),
        "pending": int(row.get("pending") or 0),
        "approval_rate": (int(row.get("approved") or 0) / total) if total else 0.0,
        "rejection_rate": (int(row.get("rejected") or 0) / total) if total else 0.0,
        "avg_review_time_seconds": (
            float(row["avg_review_s"]) if row.get("avg_review_s") else None
        ),
        "ai_agreed_count": agreed,
        "ai_overridden_count": overridden,
        "override_rate": (
            overridden / (agreed + overridden) if (agreed + overridden) else 0.0
        ),
    }


async def _outcomes(session, days: int) -> dict[str, Any]:
    from sqlalchemy import text as _t

    p = {"d": int(days)}
    row = (
        await session.execute(
            _t(
                """
                SELECT
                    COUNT(*)::int                                                          AS total_closed,
                    AVG(EXTRACT(EPOCH FROM (closed_at - opened_at)))                       AS avg_s,
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (closed_at - opened_at))
                    )                                                                      AS p50_s,
                    percentile_cont(0.9) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (closed_at - opened_at))
                    )                                                                      AS p90_s,
                    COUNT(*) FILTER (WHERE status = 'auto_closed_fp')::int                 AS fp,
                    COUNT(*) FILTER (WHERE close_reason ILIKE '%true%positive%'
                                       OR close_reason ILIKE '%confirmed%')::int           AS tp,
                    COUNT(*) FILTER (WHERE close_reason ILIKE '%suspicious%')::int         AS susp,
                    COUNT(*) FILTER (WHERE reopen_count > 0)::int                          AS reopened
                FROM investigations
                WHERE closed_at IS NOT NULL
                  AND closed_at >= now() - make_interval(days => :d)
                """
            ),
            p,
        )
    ).mappings().first() or {}
    total_closed = int(row.get("total_closed") or 0)
    return {
        "total_closed": total_closed,
        "avg_resolution_time_seconds": (
            float(row["avg_s"]) if row.get("avg_s") else None
        ),
        "p50_resolution_time_seconds": (
            float(row["p50_s"]) if row.get("p50_s") else None
        ),
        "p90_resolution_time_seconds": (
            float(row["p90_s"]) if row.get("p90_s") else None
        ),
        "closed_as_false_positive": int(row.get("fp") or 0),
        "closed_as_true_positive": int(row.get("tp") or 0),
        "closed_as_suspicious": int(row.get("susp") or 0),
        "reopen_rate": (
            (int(row.get("reopened") or 0) / total_closed) if total_closed else 0.0
        ),
    }


@router.get("/api/analytics/summary")
async def analytics_summary(
    request: Request, days: int = Query(7, ge=1, le=90)
) -> dict[str, Any]:
    identity = current_identity(request)
    period_end = datetime.now(timezone.utc)
    period_start = period_end.replace(microsecond=0) - timedelta(days=days)
    async for s in _analytics_session_for(identity):
        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "executive_kpis": await _kpis(s, days),
            "ai_behavior": await _ai_behavior(s, days),
            "human_review": await _human_review(s, days),
            "outcomes": await _outcomes(s, days),
        }
    raise HTTPException(503, "analytics session unavailable")


@router.get("/api/analytics/kpis")
async def analytics_kpis(
    request: Request, days: int = Query(7, ge=1, le=90)
) -> dict[str, Any]:
    identity = current_identity(request)
    async for s in _analytics_session_for(identity):
        return await _kpis(s, days)
    raise HTTPException(503, "analytics session unavailable")


@router.get("/api/analytics/ai-behavior")
async def analytics_ai_behavior(
    request: Request, days: int = Query(7, ge=1, le=90)
) -> dict[str, Any]:
    identity = current_identity(request)
    async for s in _analytics_session_for(identity):
        return await _ai_behavior(s, days)
    raise HTTPException(503, "analytics session unavailable")


@router.get("/api/analytics/human-review")
async def analytics_human_review(
    request: Request, days: int = Query(7, ge=1, le=90)
) -> dict[str, Any]:
    identity = current_identity(request)
    async for s in _analytics_session_for(identity):
        return await _human_review(s, days)
    raise HTTPException(503, "analytics session unavailable")


@router.get("/api/analytics/outcomes")
async def analytics_outcomes(
    request: Request, days: int = Query(7, ge=1, le=90)
) -> dict[str, Any]:
    identity = current_identity(request)
    async for s in _analytics_session_for(identity):
        return await _outcomes(s, days)
    raise HTTPException(503, "analytics session unavailable")


# ---------------------------------------------------------------------------
# /api/audit/*
# ---------------------------------------------------------------------------


async def _audit_session_for(identity: "UserIdentity"):
    from soctalk.core.tenancy.context import tenant_context
    from soctalk.core.tenancy.db import get_app_sessionmaker, get_mssp_sessionmaker

    if identity.role in _MSSP_LEVEL_ROLES:
        sm = get_mssp_sessionmaker()
        async with sm() as s:
            yield s
    else:
        sm = get_app_sessionmaker()
        async with sm() as s:
            async with tenant_context(s, identity.tenant_id):
                yield s


@router.get("/api/audit/event-types")
async def audit_event_types(request: Request) -> dict[str, list[str]]:
    from sqlalchemy import text as _t

    identity = current_identity(request)
    async for s in _audit_session_for(identity):
        rows = (
            await s.execute(_t("SELECT DISTINCT event_type FROM events ORDER BY event_type"))
        ).all()
        return {"event_types": [r[0] for r in rows]}
    raise HTTPException(503, "audit session unavailable")


@router.get("/api/audit")
async def audit_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    event_type: str | None = None,
    aggregate_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    investigation_id: str | None = None,
) -> dict[str, Any]:
    from sqlalchemy import text as _t

    identity = current_identity(request)
    conds: list[str] = []
    params: dict[str, Any] = {}
    if event_type:
        conds.append("event_type = :et")
        params["et"] = event_type
    if aggregate_type:
        conds.append("aggregate_type = :at")
        params["at"] = aggregate_type
    if investigation_id:
        conds.append("aggregate_id::text = :aid")
        params["aid"] = investigation_id
    if start_date:
        conds.append("timestamp >= :sd")
        params["sd"] = start_date
    if end_date:
        conds.append("timestamp <= :ed")
        params["ed"] = end_date
    where = (" WHERE " + " AND ".join(conds)) if conds else ""

    offset = (page - 1) * page_size
    params["lim"] = page_size
    params["off"] = offset

    async for s in _audit_session_for(identity):
        total = (
            await s.execute(_t(f"SELECT COUNT(*) FROM events{where}"), params)
        ).scalar_one()
        rows = (
            await s.execute(
                _t(
                    f"""
                    SELECT id::text, aggregate_id::text, aggregate_type, event_type,
                           version, timestamp, data
                    FROM events{where}
                    ORDER BY timestamp DESC
                    LIMIT :lim OFFSET :off
                    """
                ),
                params,
            )
        ).mappings().all()
        items = [
            {
                "id": r["id"],
                "aggregate_id": r["aggregate_id"],
                "aggregate_type": r["aggregate_type"],
                "event_type": r["event_type"],
                "version": int(r["version"]),
                "timestamp": (
                    r["timestamp"].isoformat() if r["timestamp"] else None
                ),
                "data": r["data"],
            }
            for r in rows
        ]
        return {
            "items": items,
            "total": int(total or 0),
            "page": page,
            "page_size": page_size,
            "has_more": (offset + len(items)) < int(total or 0),
        }
    raise HTTPException(503, "audit session unavailable")


@router.get("/api/audit/stats")
async def audit_stats(
    request: Request, hours: int = Query(24, ge=1, le=720)
) -> dict[str, Any]:
    from sqlalchemy import text as _t

    identity = current_identity(request)
    params = {"h": int(hours)}
    async for s in _audit_session_for(identity):
        total = (
            await s.execute(
                _t(
                    "SELECT COUNT(*) FROM events "
                    "WHERE timestamp >= now() - make_interval(hours => :h)"
                ),
                params,
            )
        ).scalar_one()
        uniq = (
            await s.execute(
                _t(
                    "SELECT COUNT(DISTINCT aggregate_id) FROM events "
                    "WHERE timestamp >= now() - make_interval(hours => :h)"
                ),
                params,
            )
        ).scalar_one()
        by_type = (
            await s.execute(
                _t(
                    "SELECT event_type, COUNT(*) FROM events "
                    "WHERE timestamp >= now() - make_interval(hours => :h) "
                    "GROUP BY event_type"
                ),
                params,
            )
        ).all()
        by_hour = (
            await s.execute(
                _t(
                    "SELECT date_trunc('hour', timestamp) AS h, COUNT(*) "
                    "FROM events "
                    "WHERE timestamp >= now() - make_interval(hours => :h) "
                    "GROUP BY h ORDER BY h"
                ),
                params,
            )
        ).all()
        return {
            "period_hours": hours,
            "total_events": int(total or 0),
            "unique_investigations": int(uniq or 0),
            "events_by_type": {r[0]: int(r[1]) for r in by_type},
            "events_by_hour": {
                (r[0].isoformat() if r[0] else ""): int(r[1]) for r in by_hour
            },
        }
    raise HTTPException(503, "audit session unavailable")


@router.get("/api/audit/investigation/{investigation_id}")
async def audit_investigation(
    investigation_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    from sqlalchemy import text as _t

    identity = current_identity(request)
    async for s in _audit_session_for(identity):
        inv = (
            await s.execute(
                _t(
                    "SELECT title, status, created_at FROM investigations "
                    "WHERE id::text = :id"
                ),
                {"id": investigation_id},
            )
        ).mappings().first()
        rows = (
            await s.execute(
                _t(
                    """
                    SELECT id::text, aggregate_type, event_type, version,
                           timestamp, data
                    FROM events
                    WHERE aggregate_id::text = :id
                    ORDER BY version ASC
                    LIMIT :lim
                    """
                ),
                {"id": investigation_id, "lim": limit},
            )
        ).mappings().all()
        events = [
            {
                "id": r["id"],
                "aggregate_type": r["aggregate_type"],
                "event_type": r["event_type"],
                "version": int(r["version"]),
                "timestamp": (
                    r["timestamp"].isoformat() if r["timestamp"] else None
                ),
                "data": r["data"],
            }
            for r in rows
        ]
        return {
            "investigation_id": investigation_id,
            "title": inv["title"] if inv else None,
            "status": inv["status"] if inv else "unknown",
            "phase": "unknown",
            "created_at": (
                inv["created_at"].isoformat()
                if inv and inv["created_at"]
                else datetime.now(timezone.utc).isoformat()
            ),
            "events": events,
            "total_events": len(events),
        }
    raise HTTPException(503, "audit session unavailable")


# ---------------------------------------------------------------------------
# /api/settings — DB-backed (integration_configs), tenant-scoped
# ---------------------------------------------------------------------------
#
# Was: single JSON file on disk, global to the whole install, never
# reaching the tenant adapter/runs-worker process (they read
# IntegrationConfig via settings_provider.py, not this file). Now: one
# row per tenant in integration_configs, same table the adapter and
# workers already read.

_SECRET_COLUMNS = {
    "shuffle_webhook_url": ("shuffle_webhook_url", "shuffle_webhook_configured"),
    "dfir_iris_api_key": ("dfir_iris_api_key_plain", "dfir_iris_api_key_configured"),
}

_INTEGRATION_CONFIG_COLUMNS = """
    llm_provider, llm_fast_model, llm_reasoning_model, llm_temperature, llm_max_tokens,
    llm_api_key_plain,
    wazuh_enabled, wazuh_url, wazuh_verify_ssl,
    wazuh_username, wazuh_password_plain, wazuh_api_token_plain,
    cortex_enabled, cortex_url, cortex_verify_ssl,
    thehive_enabled, thehive_url, thehive_organisation, thehive_verify_ssl,
    misp_enabled, misp_url, misp_verify_ssl,
    velociraptor_enabled, velociraptor_api_client_config_path,
    dfir_iris_enabled, dfir_iris_url, dfir_iris_verify_ssl, dfir_iris_api_key_plain,
    shuffle_enabled, shuffle_webhook_url,
    zeek_enabled, zeek_log_path,
    suricata_enabled, suricata_log_path, suricata_ingest_all_events,
    slack_enabled, slack_channel, slack_notify_on_escalation, slack_notify_on_verdict,
    updated_at
"""


async def _settings_tenant_id(identity: "UserIdentity"):
    from fastapi import HTTPException

    tid = _effective_review_tenant(identity)
    if tid is None:
        raise HTTPException(403, "select a tenant before reading/writing settings")
    return tid


async def _settings_session(identity: "UserIdentity"):
    from soctalk.core.tenancy.context import tenant_context
    from soctalk.core.tenancy.db import get_app_sessionmaker, get_mssp_sessionmaker

    tid = await _settings_tenant_id(identity)
    if identity.role in _MSSP_LEVEL_ROLES:
        sm = get_mssp_sessionmaker()
        async with sm() as s:
            yield s, tid
    else:
        sm = get_app_sessionmaker()
        async with sm() as s:
            async with tenant_context(s, tid):
                yield s, tid


def _settings_row_to_dict(row: Any, tenant_id: Any) -> dict[str, Any]:
    return {
        "id": str(tenant_id),
        "readonly": False,
        "sources": {},
        "llm_provider": row["llm_provider"],
        "llm_fast_model": row["llm_fast_model"],
        "llm_reasoning_model": row["llm_reasoning_model"],
        "llm_temperature": float(row["llm_temperature"]),
        "llm_max_tokens": int(row["llm_max_tokens"]),
        "llm_anthropic_base_url": None,
        "llm_openai_base_url": None,
        "llm_openai_organization": None,
        "anthropic_api_key_configured": bool(row["llm_api_key_plain"]),
        "openai_api_key_configured": bool(row["llm_api_key_plain"]),
        "llm_keys_conflict": False,
        "wazuh_enabled": bool(row["wazuh_enabled"]),
        "wazuh_url": row["wazuh_url"],
        "wazuh_verify_ssl": bool(row["wazuh_verify_ssl"]),
        "wazuh_credentials_configured": bool(
            row["wazuh_password_plain"] or row["wazuh_api_token_plain"]
        ),
        "cortex_enabled": bool(row["cortex_enabled"]),
        "cortex_url": row["cortex_url"],
        "cortex_verify_ssl": bool(row["cortex_verify_ssl"]),
        "cortex_api_key_configured": False,
        "thehive_enabled": bool(row["thehive_enabled"]),
        "thehive_url": row["thehive_url"],
        "thehive_organisation": row["thehive_organisation"],
        "thehive_verify_ssl": bool(row["thehive_verify_ssl"]),
        "thehive_api_key_configured": False,
        "misp_enabled": bool(row["misp_enabled"]),
        "misp_url": row["misp_url"],
        "misp_verify_ssl": bool(row["misp_verify_ssl"]),
        "misp_api_key_configured": False,
        "velociraptor_enabled": bool(row["velociraptor_enabled"]),
        "velociraptor_api_client_config_path": row["velociraptor_api_client_config_path"],
        "velociraptor_credentials_configured": bool(
            row["velociraptor_api_client_config_path"]
        ),
        "dfir_iris_enabled": bool(row["dfir_iris_enabled"]),
        "dfir_iris_url": row["dfir_iris_url"],
        "dfir_iris_verify_ssl": bool(row["dfir_iris_verify_ssl"]),
        "dfir_iris_api_key_configured": bool(row["dfir_iris_api_key_plain"]),
        "shuffle_enabled": bool(row["shuffle_enabled"]),
        "shuffle_webhook_configured": bool(row["shuffle_webhook_url"]),
        "zeek_enabled": bool(row["zeek_enabled"]),
        "zeek_ingest_path": row["zeek_log_path"],
        "suricata_enabled": bool(row["suricata_enabled"]),
        "suricata_ingest_path": row["suricata_log_path"],
        "suricata_ingest_all_events": bool(row["suricata_ingest_all_events"]),
        "slack_enabled": bool(row["slack_enabled"]),
        "slack_channel": row["slack_channel"],
        "slack_notify_on_escalation": bool(row["slack_notify_on_escalation"]),
        "slack_notify_on_verdict": bool(row["slack_notify_on_verdict"]),
        "slack_webhook_configured": False,
        "updated_at": (
            row["updated_at"].isoformat() if row["updated_at"] else None
        ),
    }


@router.get("/api/settings")
async def settings_get(request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    async for s, tid in _settings_session(identity):
        row = (
            await s.execute(
                text(f"SELECT {_INTEGRATION_CONFIG_COLUMNS} FROM integration_configs "
                     "WHERE tenant_id = :tid"),
                {"tid": str(tid)},
            )
        ).mappings().first()
        if row is None:
            await s.execute(
                text("INSERT INTO integration_configs (id, tenant_id) "
                     "VALUES (gen_random_uuid(), :tid) "
                     "ON CONFLICT (tenant_id) DO NOTHING"),
                {"tid": str(tid)},
            )
            await s.commit()
            row = (
                await s.execute(
                    text(f"SELECT {_INTEGRATION_CONFIG_COLUMNS} FROM integration_configs "
                         "WHERE tenant_id = :tid"),
                    {"tid": str(tid)},
                )
            ).mappings().first()
        return _settings_row_to_dict(row, tid)
    raise HTTPException(503, "settings session unavailable")


class _SettingsSaveBody(BaseModel):
    llm_provider: str | None = None
    llm_fast_model: str | None = None
    llm_reasoning_model: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    wazuh_enabled: bool | None = None
    wazuh_url: str | None = None
    wazuh_verify_ssl: bool | None = None
    cortex_enabled: bool | None = None
    cortex_url: str | None = None
    cortex_verify_ssl: bool | None = None
    thehive_enabled: bool | None = None
    thehive_url: str | None = None
    thehive_organisation: str | None = None
    thehive_verify_ssl: bool | None = None
    misp_enabled: bool | None = None
    misp_url: str | None = None
    misp_verify_ssl: bool | None = None
    velociraptor_enabled: bool | None = None
    velociraptor_api_client_config_path: str | None = None
    dfir_iris_enabled: bool | None = None
    dfir_iris_url: str | None = None
    dfir_iris_verify_ssl: bool | None = None
    dfir_iris_api_key: str | None = None
    shuffle_enabled: bool | None = None
    shuffle_webhook_url: str | None = None
    zeek_enabled: bool | None = None
    zeek_ingest_path: str | None = None
    suricata_enabled: bool | None = None
    suricata_ingest_path: str | None = None
    suricata_ingest_all_events: bool | None = None
    slack_enabled: bool | None = None
    slack_channel: str | None = None
    slack_notify_on_escalation: bool | None = None
    slack_notify_on_verdict: bool | None = None


_FIELD_TO_COLUMN = {
    "zeek_ingest_path": "zeek_log_path",
    "suricata_ingest_path": "suricata_log_path",
}


@router.put("/api/settings")
async def settings_put(body: _SettingsSaveBody, request: Request) -> dict[str, Any]:
    from fastapi import HTTPException

    identity = current_identity(request)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        async for s, tid in _settings_session(identity):
            row = (
                await s.execute(
                    text(f"SELECT {_INTEGRATION_CONFIG_COLUMNS} FROM integration_configs "
                         "WHERE tenant_id = :tid"),
                    {"tid": str(tid)},
                )
            ).mappings().first()
            if row is None:
                raise HTTPException(404, "settings not found for tenant")
            d = _settings_row_to_dict(row, tid)
            return {"success": True, "updated_at": d["updated_at"]}
        raise HTTPException(503, "settings session unavailable")

    if "dfir_iris_api_key" in payload:
        payload["dfir_iris_api_key_plain"] = payload.pop("dfir_iris_api_key")

    set_clauses = []
    params: dict[str, Any] = {}
    for field, value in payload.items():
        column = _FIELD_TO_COLUMN.get(field, field)
        set_clauses.append(f"{column} = :{column}")
        params[column] = value
    set_clauses.append("updated_at = now()")

    async for s, tid in _settings_session(identity):
        params["tid"] = str(tid)
        await s.execute(
            text("INSERT INTO integration_configs (id, tenant_id) "
                 "VALUES (gen_random_uuid(), :tid) "
                 "ON CONFLICT (tenant_id) DO NOTHING"),
            {"tid": str(tid)},
        )
        await s.execute(
            text(f"UPDATE integration_configs SET {', '.join(set_clauses)} "
                 "WHERE tenant_id = :tid"),
            params,
        )
        await s.commit()
        row = (
            await s.execute(
                text(f"SELECT {_INTEGRATION_CONFIG_COLUMNS} FROM integration_configs "
                     "WHERE tenant_id = :tid"),
                {"tid": str(tid)},
            )
        ).mappings().first()
        d = _settings_row_to_dict(row, tid)
        return {"success": True, "updated_at": d["updated_at"]}
    raise HTTPException(503, "settings session unavailable")


__all__ = ["router"]
