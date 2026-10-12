"""Add durable historical analytics submissions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261012_0012"
down_revision = "20261011_0011"
branch_labels = None
depends_on = None


def upgrade():
    submission_status = postgresql.ENUM(
        "PENDING",
        "SENDING",
        "PROCESSED",
        "CONFLICT",
        "TECHNICAL_ERROR",
        name="historical_submission_status",
        create_type=False,
    )
    submission_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "historical_submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("time_zone", sa.String(length=128), nullable=False),
        sa.Column("measurement_version", sa.String(length=100), nullable=False),
        sa.Column("bridge_version", sa.String(length=100), nullable=False),
        sa.Column("tzdata_version", sa.String(length=50), nullable=False),
        sa.Column("series_key", sa.String(length=71), nullable=False),
        sa.Column("representation_record_id", sa.String(length=68), nullable=False),
        sa.Column("interval_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            submission_status,
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "analytics_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("analytics_evaluation_id", sa.String(length=128), nullable=True),
        sa.Column("analytics_replayed", sa.Boolean(), nullable=True),
        sa.Column(
            "retryable",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "assignment_id = device_id",
            name="ck_historical_submissions_assignment_is_device",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_historical_submissions_attempts_non_negative",
        ),
        sa.CheckConstraint(
            "interval_start < interval_end",
            name="ck_historical_submissions_interval",
        ),
        sa.ForeignKeyConstraint(["assignment_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
        sa.UniqueConstraint("representation_record_id"),
        sa.UniqueConstraint(
            "series_key",
            "local_date",
            name="uq_historical_submissions_series_local_date",
        ),
    )
    op.create_index(
        "ix_historical_submissions_profile_id",
        "historical_submissions",
        ["profile_id"],
    )
    op.create_index(
        "ix_historical_submissions_device_id",
        "historical_submissions",
        ["device_id"],
    )
    op.create_index(
        "ix_historical_submissions_assignment_id",
        "historical_submissions",
        ["assignment_id"],
    )
    op.create_index(
        "ix_historical_submissions_series_key",
        "historical_submissions",
        ["series_key"],
    )
    op.create_index(
        "ix_historical_submissions_status",
        "historical_submissions",
        ["status"],
    )


def downgrade():
    op.drop_index("ix_historical_submissions_status", table_name="historical_submissions")
    op.drop_index("ix_historical_submissions_series_key", table_name="historical_submissions")
    op.drop_index("ix_historical_submissions_assignment_id", table_name="historical_submissions")
    op.drop_index("ix_historical_submissions_device_id", table_name="historical_submissions")
    op.drop_index("ix_historical_submissions_profile_id", table_name="historical_submissions")
    op.drop_table("historical_submissions")
    postgresql.ENUM(name="historical_submission_status").drop(
        op.get_bind(),
        checkfirst=True,
    )
