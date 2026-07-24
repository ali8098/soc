"""Partial indexes for flight-recorder aggregate queries (issue #72).

The fleet-day endpoint scans investigation_events by (tenant, kind, day)
for terminal closes and guard rulings. Partial composite keeps the index
tiny relative to the full event log.
"""

from __future__ import annotations

from alembic import op

revision = "v1_0038_replay_event_indexes"
down_revision: str | None = "v1_0037_authored_response_playbooks"
branch_labels = None
depends_on = None

_INDEX = "ix_inv_events_replay_kind_day"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {_INDEX}
        ON investigation_events (tenant_id, kind, created_at)
        WHERE kind IN ('auto_closed', 'guard_evaluated')
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
