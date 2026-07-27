"""Transient-retry bookkeeping on investigation_runs (issue #77).

A run that fails on a TRANSIENT provider error (e.g. a scale-to-zero serverless
endpoint returning 404 / no-workers while it cold-starts) should be released
back to the queue and retried on the SAME run_id, not marked terminally failed.
Terminal failure only after the attempt cap, so completed-run side effects
(response dispatch, HIL review, memoization — all keyed on run_id) never
replay and never get skipped for a warming backend.

Adds:
  * attempts            — how many times this run has been claimed+released
  * max_attempts        — per-run cap (default 4)
  * last_error_category — the classify_llm_error bucket of the most recent
                          transient release, for observability / gating

Existing rows default to attempts=0, max_attempts=4, category NULL — no
behavior change for anything already queued.

Revision ID: v1_0039_run_transient_retry
Revises: v1_0038_replay_event_indexes
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "v1_0039_run_transient_retry"
down_revision: str | None = "v1_0038_replay_event_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "investigation_runs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "investigation_runs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="4"),
    )
    op.add_column(
        "investigation_runs",
        sa.Column("last_error_category", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("investigation_runs", "last_error_category")
    op.drop_column("investigation_runs", "max_attempts")
    op.drop_column("investigation_runs", "attempts")
