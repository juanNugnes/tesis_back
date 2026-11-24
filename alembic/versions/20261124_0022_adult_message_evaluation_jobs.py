"""Persist controlled adult-message evaluation transport jobs."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261124_0022"
down_revision = "20261122_0021"
branch_labels = None
depends_on = None


job_status = sa.Enum(
    "PENDING",
    "SENDING",
    "PROCESSED",
    "CONFLICT",
    "TECHNICAL_ERROR",
    name="adult_message_evaluation_job_status",
)


def upgrade():
    op.create_table(
        "adult_message_evaluation_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fixture_id", sa.String(128), nullable=False, unique=True),
        sa.Column("fixture_sha256", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey("profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("series_id", sa.String(128), nullable=False),
        sa.Column("evaluation_date", sa.Date(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("status", job_status, nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "analytics_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("analytics_evaluation_id", sa.String(128), nullable=True),
        sa.Column("analytics_card_id", sa.String(64), nullable=True),
        sa.Column("analytics_card_version", sa.Integer(), nullable=True),
        sa.Column("analytics_replayed", sa.Boolean(), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_adult_message_evaluation_jobs_attempts_non_negative",
        ),
    )
    op.create_index(
        "ix_adult_message_evaluation_jobs_profile_id",
        "adult_message_evaluation_jobs",
        ["profile_id"],
    )
    op.create_index(
        "ix_adult_message_evaluation_jobs_series_id",
        "adult_message_evaluation_jobs",
        ["series_id"],
    )
    op.create_index(
        "ix_adult_message_evaluation_jobs_status",
        "adult_message_evaluation_jobs",
        ["status"],
    )
    op.create_index(
        "ix_adult_message_evaluation_jobs_profile_status",
        "adult_message_evaluation_jobs",
        ["profile_id", "status"],
    )
    op.create_index(
        "ix_adult_message_evaluation_jobs_series_date",
        "adult_message_evaluation_jobs",
        ["series_id", "evaluation_date"],
    )


def downgrade():
    op.drop_index(
        "ix_adult_message_evaluation_jobs_series_date",
        table_name="adult_message_evaluation_jobs",
    )
    op.drop_index(
        "ix_adult_message_evaluation_jobs_profile_status",
        table_name="adult_message_evaluation_jobs",
    )
    op.drop_index(
        "ix_adult_message_evaluation_jobs_status",
        table_name="adult_message_evaluation_jobs",
    )
    op.drop_index(
        "ix_adult_message_evaluation_jobs_series_id",
        table_name="adult_message_evaluation_jobs",
    )
    op.drop_index(
        "ix_adult_message_evaluation_jobs_profile_id",
        table_name="adult_message_evaluation_jobs",
    )
    op.drop_table("adult_message_evaluation_jobs")
    job_status.drop(op.get_bind(), checkfirst=True)
