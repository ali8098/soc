"""Shuffle → SocTalk callback endpoint.

Shuffle SOAR workflows can POST results back here after executing an action
(e.g. endpoint isolated, ticket created, block rule pushed). SocTalk records
the result as a system note on the investigation so analysts have a full
audit trail without leaving the platform.

Security model:
- HMAC-SHA256 signature verified against ``shuffle_webhook_secret`` tenant
  policy (same secret used for outbound notify_shuffle calls).
- Tenant is identified from the URL path (not the body) so a misconfigured
  Shuffle workflow can't spoof another tenant's investigation.
- No authentication cookie/session required — this is a machine-to-machine
  endpoint. Rate limiting is the caller's problem at the ingress layer.

Endpoint:
    POST /api/internal/shuffle/callback/{tenant_id}

Body (JSON):
    {
        "execution_id":     "shuffle-exec-abc123",   # Shuffle execution ID
        "investigation_id": "uuid",                  # SocTalk investigation ID
        "workflow_name":    "Isolate Endpoint",       # human label
        "status":           "FINISHED",               # Shuffle status string
        "result":           { ... }                   # arbitrary Shuffle output
    }

Response:
    200 {"ok": true, "note_id": "uuid"}
    400 bad body / missing fields
    401 signature mismatch
    404 investigation not found in tenant
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from soctalk.core.tenancy.db import get_app_sessionmaker
from soctalk.core.ir.policies import effective_policy

logger = structlog.get_logger()

router = APIRouter(prefix="/api/internal/shuffle", tags=["shuffle-callback"])

_MAX_NOTE = 4096


class ShuffleCallbackBody(BaseModel):
    """Expected body from Shuffle workflow callback."""

    execution_id: str = Field(..., description="Shuffle execution ID")
    investigation_id: str = Field(..., description="SocTalk investigation UUID")
    workflow_name: str = Field(default="", description="Human-readable workflow name")
    status: str = Field(default="FINISHED", description="Shuffle execution status")
    result: dict[str, Any] = Field(
        default_factory=dict, description="Shuffle workflow output"
    )


def _verify_signature(secret: str, raw_body: bytes, sig_header: str) -> bool:
    """Verify HMAC-SHA256 signature — same scheme as outbound notify_shuffle."""
    if not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = sig_header[len("sha256="):]
    return hmac.compare_digest(expected, received)


@router.post("/callback/{tenant_id}", summary="Receive Shuffle workflow result")
async def shuffle_callback(
    tenant_id: UUID,
    request: Request,
    x_soctalk_signature: str | None = Header(default=None, alias="X-SocTalk-Signature"),
) -> dict[str, Any]:
    """Receive a Shuffle SOAR workflow result and record it as a note.

    Args:
        tenant_id: Tenant UUID from URL path.
        request:   Raw FastAPI request (needed to read raw body for sig check).
        x_soctalk_signature: Optional HMAC signature header.

    Returns:
        JSON with ok=True and note_id on success.

    Raises:
        401: Signature mismatch when secret is configured.
        400: Missing required fields.
        404: Investigation not found in tenant.
    """
    raw_body = await request.body()

    # Parse body
    try:
        data = json.loads(raw_body)
        body = ShuffleCallbackBody(**data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid body: {exc}") from exc

    sm = get_app_sessionmaker()
    async with sm() as db:
        # Verify HMAC signature if secret is configured
        policy = await effective_policy(db, tenant_id)
        secret = str(policy.get("shuffle_webhook_secret") or "").strip()
        if secret:
            if not x_soctalk_signature:
                raise HTTPException(
                    status_code=401,
                    detail="X-SocTalk-Signature header required",
                )
            if not _verify_signature(secret, raw_body, x_soctalk_signature):
                logger.warning(
                    "shuffle_callback_signature_mismatch",
                    tenant_id=str(tenant_id),
                    execution_id=body.execution_id,
                )
                raise HTTPException(status_code=401, detail="Signature mismatch")

        # Verify investigation exists in this tenant
        row = await db.execute(
            text(
                "SELECT id FROM cases "
                "WHERE id = :iid AND tenant_id = :tid LIMIT 1"
            ),
            {"iid": body.investigation_id, "tid": str(tenant_id)},
        )
        if not row.fetchone():
            raise HTTPException(
                status_code=404,
                detail=f"Investigation {body.investigation_id} not found in tenant",
            )

        # Build note body
        result_snippet = json.dumps(body.result, default=str)[:800]
        note_body = (
            f"[Shuffle callback] Workflow: {body.workflow_name}\n"
            f"Execution ID: {body.execution_id}\n"
            f"Status: {body.status}\n"
            f"Result: {result_snippet}"
        )[:_MAX_NOTE]

        note_id = str(uuid4())
        await db.execute(
            text(
                "INSERT INTO notes "
                "(id, tenant_id, investigation_id, author_kind, author_id, body, visibility) "
                "VALUES (:id, :tid, :iid, 'system', :author, :body, 'mssp_only')"
            ),
            {
                "id": note_id,
                "tid": str(tenant_id),
                "iid": body.investigation_id,
                "author": f"shuffle:{body.execution_id}",
                "body": note_body,
            },
        )
        await db.commit()

    logger.info(
        "shuffle_callback_recorded",
        tenant_id=str(tenant_id),
        investigation_id=body.investigation_id,
        execution_id=body.execution_id,
        status=body.status,
        note_id=note_id,
    )

    return {"ok": True, "note_id": note_id}
