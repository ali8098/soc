"""Fleet-day aggregate for the flight recorder (issue #72, Phase 3 data).

One day of the pipeline at a glance: exact counters classified from
EXPLICIT replay-event payloads (terminal-close ``path``, guard
``effect``), escalations from ``pending_reviews.ai_decision`` (the same
signal the existing analytics use), plus a deterministic sample of real
alert "dots" for the fleet map — every dot carries a real alert id and
investigation id; ``sample_rate`` is disclosed so the UI can say
"showing 1 in N". Counters never come from the sample.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from soctalk.core.tenancy.auth import current_identity
from soctalk.core.tenancy.context import tenant_context
from soctalk.core.tenancy.db import get_app_sessionmaker

logger = structlog.get_logger()

router = APIRouter(tags=["fleet-day"], dependencies=[Depends(current_identity)])


class FleetDot(BaseModel):
    alert_id: str
    investigation_id: str | None
    first_event_at: str
    closed_at: str | None
    path: str | None
    outcome: str
    veto: bool


class FleetVetoRow(BaseModel):
    investigation_id: str
    at: str
    stage: str | None
    fired: list[str]


class FleetDayResponse(BaseModel):
    date: str
    tz: str
    server_now: str
    window_start: str
    window_end: str
    ingested: int
    closed_ingest_memoized: int
    closed_ingest_rules: int
    closed_operational: int
    closed_reasoning: int
    escalated: int
    guard_vetoes: int
    still_open: int
    ingest_histogram: list[int]  # 24 hourly buckets, local tz
    dollars_used: float
    tokens_used: int
    sample_rate: float
    dots: list[FleetDot]
    recent_vetoes: list[FleetVetoRow]


@router.get("/api/analytics/fleet-day", response_model=FleetDayResponse)
async def fleet_day(
    request: Request,
    date: date_type | None = Query(None, description="Local date; default today"),
    tz: str = Query("UTC", max_length=64),
    sample_limit: int = Query(500, ge=1, le=2000),
) -> FleetDayResponse:
    identity = current_identity(request)
    if identity is None:
        raise HTTPException(401, "authentication required")
    if identity.tenant_id is None:
        raise HTTPException(403, "tenant scope required")

    try:
        zone = ZoneInfo(tz)
    except Exception:
        raise HTTPException(400, f"unknown timezone: {tz}") from None

    local_now = datetime.now(zone)
    day = date or local_now.date()
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    end = start + timedelta(days=1)

    sm = get_app_sessionmaker()
    async with sm() as db, tenant_context(db, identity.tenant_id):
        p: dict[str, Any] = {"s": start, "e": end, "tz": tz}

        server_now = (await db.execute(text("SELECT now()"))).scalar_one()

        alerts_row = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*)::int AS ingested
                    FROM alerts
                    WHERE first_event_at >= :s AND first_event_at < :e
                    """
                ),
                p,
            )
        ).mappings().one()

        hist_rows = (
            await db.execute(
                text(
                    """
                    SELECT extract(hour FROM first_event_at AT TIME ZONE :tz)::int AS h,
                           COUNT(*)::int AS n
                    FROM alerts
                    WHERE first_event_at >= :s AND first_event_at < :e
                    GROUP BY h
                    """
                ),
                p,
            )
        ).mappings().all()
        histogram = [0] * 24
        for r in hist_rows:
            if 0 <= int(r["h"]) < 24:
                histogram[int(r["h"])] = int(r["n"])

        close_rows = (
            await db.execute(
                text(
                    """
                    SELECT payload->>'path' AS path, COUNT(*)::int AS n
                    FROM investigation_events
                    WHERE kind = 'auto_closed'
                      AND created_at >= :s AND created_at < :e
                    GROUP BY path
                    """
                ),
                p,
            )
        ).mappings().all()
        closes = {r["path"]: int(r["n"]) for r in close_rows}

        veto_row = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*)::int AS n
                    FROM investigation_events
                    WHERE kind = 'guard_evaluated'
                      AND payload->>'effect' = 'override'
                      AND created_at >= :s AND created_at < :e
                    """
                ),
                p,
            )
        ).mappings().one()

        recent_veto_rows = (
            await db.execute(
                text(
                    """
                    SELECT investigation_id, created_at, payload
                    FROM investigation_events
                    WHERE kind = 'guard_evaluated'
                      AND payload->>'effect' = 'override'
                      AND created_at >= :s AND created_at < :e
                    ORDER BY created_at DESC
                    LIMIT 8
                    """
                ),
                p,
            )
        ).mappings().all()

        escalated_row = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*)::int AS n
                    FROM pending_reviews
                    WHERE ai_decision = 'escalate'
                      AND created_at >= :s AND created_at < :e
                    """
                ),
                p,
            )
        ).mappings().one()

        spend_row = (
            await db.execute(
                text(
                    """
                    SELECT COALESCE(SUM(dollars_used), 0)::float AS dollars,
                           COALESCE(SUM(tokens_used), 0)::bigint AS tokens
                    FROM investigation_runs
                    WHERE started_at >= :s AND started_at < :e
                    """
                ),
                p,
            )
        ).mappings().one()

        # Deterministic dot sample: md5-ordered so repeated polls return the
        # same alerts (a live view must not reshuffle its dots).
        dot_rows = (
            await db.execute(
                text(
                    """
                    SELECT a.id AS alert_id,
                           a.investigation_id,
                           a.first_event_at,
                           i.closed_at,
                           i.status AS inv_status,
                           ce.payload->>'path' AS path,
                           EXISTS (
                             SELECT 1 FROM investigation_events ge
                             WHERE ge.investigation_id = a.investigation_id
                               AND ge.kind = 'guard_evaluated'
                               AND ge.payload->>'effect' = 'override'
                           ) AS veto,
                           EXISTS (
                             SELECT 1 FROM pending_reviews pr
                             WHERE pr.investigation_id = a.investigation_id
                               AND pr.ai_decision = 'escalate'
                           ) AS escalated
                    FROM alerts a
                    LEFT JOIN investigations i ON i.id = a.investigation_id
                    LEFT JOIN LATERAL (
                      SELECT payload FROM investigation_events ev
                      WHERE ev.investigation_id = a.investigation_id
                        AND ev.kind = 'auto_closed'
                      ORDER BY ev.seq DESC LIMIT 1
                    ) ce ON true
                    WHERE a.first_event_at >= :s AND a.first_event_at < :e
                    ORDER BY md5(a.id::text)
                    LIMIT :lim
                    """
                ),
                {**p, "lim": sample_limit},
            )
        ).mappings().all()

        dots = []
        for r in dot_rows:
            if r["path"]:
                outcome = "closed"
            elif r["escalated"]:
                outcome = "human"
            elif r["inv_status"] == "auto_closed_fp":
                # Terminal row without a replay beat: closed before Phase 0
                # instrumentation existed. Honest label, not a guess.
                outcome = "closed_unrecorded"
            else:
                outcome = "open"
            dots.append(
                FleetDot(
                    alert_id=str(r["alert_id"]),
                    investigation_id=(
                        str(r["investigation_id"]) if r["investigation_id"] else None
                    ),
                    first_event_at=r["first_event_at"].isoformat(),
                    closed_at=r["closed_at"].isoformat() if r["closed_at"] else None,
                    path=r["path"],
                    outcome=outcome,
                    veto=bool(r["veto"]),
                )
            )

    ingested = int(alerts_row["ingested"])
    closed_total = sum(closes.values())
    escalated = int(escalated_row["n"])
    return FleetDayResponse(
        date=day.isoformat(),
        tz=tz,
        server_now=server_now.isoformat(),
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        ingested=ingested,
        closed_ingest_memoized=closes.get("ingest_memoized", 0),
        closed_ingest_rules=closes.get("ingest_rules", 0),
        closed_operational=closes.get("operational", 0),
        closed_reasoning=closes.get("reasoning", 0),
        escalated=escalated,
        guard_vetoes=int(veto_row["n"]),
        still_open=max(0, ingested - closed_total - escalated),
        ingest_histogram=histogram,
        dollars_used=float(spend_row["dollars"]),
        tokens_used=int(spend_row["tokens"]),
        sample_rate=(min(1.0, sample_limit / ingested) if ingested else 1.0),
        dots=dots,
        recent_vetoes=[
            FleetVetoRow(
                investigation_id=str(r["investigation_id"]),
                at=r["created_at"].isoformat(),
                stage=(r["payload"] or {}).get("stage"),
                fired=list((r["payload"] or {}).get("fired") or []),
            )
            for r in recent_veto_rows
        ],
    )
