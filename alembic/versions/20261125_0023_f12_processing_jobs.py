"""Add durable automatic F12 processing jobs."""

from alembic import op
import sqlalchemy as sa


revision = "20261125_0023"
down_revision = "20261124_0022"
branch_labels = None
depends_on = None


job_status = sa.Enum(
    "PENDING",
    "PROCESSING",
    "COMPLETED",
    "BLOCKED",
    "TECHNICAL_ERROR",
    "MANUAL_REVIEW",
    name="f12_processing_job_status",
)


def upgrade():
    op.create_table(
        "f12_processing_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "evidence_id",
            sa.Uuid(),
            sa.ForeignKey("f12_raw_evidence_captures.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", job_status, nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_message", sa.String(500), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_f12_processing_jobs_attempts_non_negative",
        ),
    )
    op.create_index(
        "ix_f12_processing_jobs_evidence_id",
        "f12_processing_jobs",
        ["evidence_id"],
        unique=True,
    )
    op.create_index(
        "ix_f12_processing_jobs_status",
        "f12_processing_jobs",
        ["status"],
    )
    op.create_index(
        "ix_f12_processing_jobs_status_retry",
        "f12_processing_jobs",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_f12_processing_jobs_lease",
        "f12_processing_jobs",
        ["lease_expires_at"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO f12_processing_jobs (
                id, evidence_id, status, attempts, retryable,
                last_error_code, last_error_message, created_at, updated_at
            )
            SELECT
                gen_random_uuid(), id,
                CASE
                    WHEN evidence_state = 'AVAILABLE' AND revision = 1
                        THEN 'PENDING'::f12_processing_job_status
                    ELSE 'BLOCKED'::f12_processing_job_status
                END,
                0, false,
                CASE
                    WHEN evidence_state = 'UNAVAILABLE' THEN 'unavailable_policy_undefined'
                    WHEN revision <> 1 THEN 'corrected_revision_policy_undefined'
                    ELSE NULL
                END,
                CASE
                    WHEN evidence_state = 'UNAVAILABLE'
                        THEN 'UNAVAILABLE evidence is retained but is not processed'
                    WHEN revision <> 1
                        THEN 'Corrected evidence revisions are retained but are not processed'
                    ELSE NULL
                END,
                received_at, received_at
            FROM f12_raw_evidence_captures
            """
        )
    )


def downgrade():
    op.drop_index("ix_f12_processing_jobs_lease", table_name="f12_processing_jobs")
    op.drop_index("ix_f12_processing_jobs_status_retry", table_name="f12_processing_jobs")
    op.drop_index("ix_f12_processing_jobs_status", table_name="f12_processing_jobs")
    op.drop_index("ix_f12_processing_jobs_evidence_id", table_name="f12_processing_jobs")
    op.drop_table("f12_processing_jobs")
    job_status.drop(op.get_bind(), checkfirst=True)
