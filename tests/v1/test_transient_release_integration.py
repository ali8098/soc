"""Release-for-retry endpoint semantics against a real DB (#77, Phase 1).

Acceptance properties, proven against Postgres by driving the real
``release_run`` handler:

  * a transient release keeps the run ``active`` on the SAME run_id, bumps
    ``attempts``, records the category, clears the lease, and pushes
    ``not_before`` into the future (backoff) so it is not instantly re-claimed;
  * a run stays claimable across releases (cold-start survival), and only
    terminalizes to ``failed`` once ``attempts`` hits ``max_attempts``;
  * a release with the wrong lease is a 409 (lease safety), so a reclaimed
    run cannot be double-released.
"""

from __future__ import annotations

import os
import types
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from soctalk.core.api.worker_runs import ReleasePayload, release_run
from soctalk.core.ir.runtime import start_run

SKIP_INTEGRATION = os.getenv("SKIP_INTEGRATION", "0") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(SKIP_INTEGRATION, reason="needs Postgres"),
]


async def _mk_investigation(s: AsyncSession, tenant_id) -> str:
    cid = str(uuid4())
    await s.execute(
        text(
            "INSERT INTO investigations (id, tenant_id, short_id, title, status, "
            "severity, opened_at, visibility) "
            "VALUES (:id, :t, :sid, 'x', 'active', 5, now(), 'mssp_only')"
        ),
        {"id": cid, "t": str(tenant_id), "sid": f"S-{cid[:6]}"},
    )
    return cid


async def _lease(s: AsyncSession, run_id, *, max_attempts: int | None = None) -> UUID:
    """Give a run an active lease, as a claim would."""
    lease_id = uuid4()
    q = (
        "UPDATE investigation_runs "
        "SET status='active', lease_id=:lid, claimed_at=now(), "
        "    lease_expires_at=now() + interval '60 seconds', "
        "    not_before='1970-01-01T00:00:00Z'"
    )
    params = {"lid": str(lease_id), "id": str(run_id)}
    if max_attempts is not None:
        q += ", max_attempts=:ma"
        params["ma"] = max_attempts
    q += " WHERE id=:id"
    await s.execute(text(q), params)
    return lease_id


async def _row(s: AsyncSession, run_id) -> dict:
    return dict((await s.execute(
        text("SELECT status, attempts, max_attempts, last_error_category, "
             "lease_id, not_before FROM investigation_runs WHERE id=:r"),
        {"r": str(run_id)},
    )).mappings().one())


def _req(session: AsyncSession, tenant_id, monkeypatch):
    """A fake Request whose _db/_verify_worker_jwt the handler will use."""
    import soctalk.core.api.worker_runs as wr
    monkeypatch.setattr(wr, "_db", lambda _r: session)
    monkeypatch.setattr(wr, "_verify_worker_jwt", lambda _r: tenant_id)
    return types.SimpleNamespace(headers={}, state=types.SimpleNamespace())


async def test_release_keeps_run_active_and_reclaimable(
    mssp_session: AsyncSession, seed_two_tenants, monkeypatch
):
    tenant_a, _ = seed_two_tenants
    cid = await _mk_investigation(mssp_session, tenant_a.tenant_id)
    run_id = await start_run(mssp_session, tenant_a.tenant_id, cid, settle_seconds=0)
    lease = await _lease(mssp_session, run_id, max_attempts=4)
    await mssp_session.commit()

    req = _req(mssp_session, tenant_a.tenant_id, monkeypatch)
    out = await release_run(run_id, ReleasePayload(
        lease_id=lease, error_category="serverless_unavailable"), req)

    assert out["retrying"] is True and out["attempts"] == 1
    row = await _row(mssp_session, run_id)
    assert row["status"] == "active", "transient release must NOT terminalize the run"
    assert row["attempts"] == 1
    assert row["last_error_category"] == "serverless_unavailable"
    assert row["lease_id"] is None, "lease must be cleared for reclaim"
    # backoff pushed not_before into the future so it isn't instantly re-claimed
    from datetime import datetime, timezone
    assert row["not_before"] > datetime.now(timezone.utc)


async def test_release_terminalizes_at_attempt_cap(
    mssp_session: AsyncSession, seed_two_tenants, monkeypatch
):
    tenant_a, _ = seed_two_tenants
    cid = await _mk_investigation(mssp_session, tenant_a.tenant_id)
    run_id = await start_run(mssp_session, tenant_a.tenant_id, cid, settle_seconds=0)
    # cap of 2: first release keeps active, second terminalizes.
    lease = await _lease(mssp_session, run_id, max_attempts=2)
    await mssp_session.commit()

    req = _req(mssp_session, tenant_a.tenant_id, monkeypatch)
    out1 = await release_run(run_id, ReleasePayload(
        lease_id=lease, error_category="serverless_unavailable"), req)
    assert out1["retrying"] is True and (await _row(mssp_session, run_id))["status"] == "active"

    lease2 = await _lease(mssp_session, run_id, max_attempts=2)  # re-claim
    await mssp_session.commit()
    out2 = await release_run(run_id, ReleasePayload(
        lease_id=lease2, error_category="serverless_unavailable"), req)
    assert out2["retrying"] is False, "must terminalize once attempts >= max_attempts"
    row = await _row(mssp_session, run_id)
    assert row["status"] == "failed" and row["attempts"] == 2


async def test_release_wrong_lease_is_409(
    mssp_session: AsyncSession, seed_two_tenants, monkeypatch
):
    from fastapi import HTTPException
    tenant_a, _ = seed_two_tenants
    cid = await _mk_investigation(mssp_session, tenant_a.tenant_id)
    run_id = await start_run(mssp_session, tenant_a.tenant_id, cid, settle_seconds=0)
    await _lease(mssp_session, run_id, max_attempts=4)
    await mssp_session.commit()

    req = _req(mssp_session, tenant_a.tenant_id, monkeypatch)
    with pytest.raises(HTTPException) as ei:
        await release_run(run_id, ReleasePayload(
            lease_id=uuid4(), error_category="serverless_unavailable"), req)
    assert ei.value.status_code == 409
