"""Add traceable, idempotent aggregate usage snapshots."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261016_0013"
down_revision = "20261012_0012"
branch_labels = None
depends_on = None


METRIC_STATUSES = (
    "MEASURED",
    "UNAVAILABLE",
    "PERMISSION_DENIED",
    "CAPTURE_ERROR",
    "INCOMPLETE",
)
COVERAGE_STATUSES = ("UNKNOWN", "PARTIAL")


def upgrade():
    bind = op.get_bind()
    metric_status = postgresql.ENUM(
        *METRIC_STATUSES,
        name="usage_metric_status",
        create_type=False,
    )
    coverage_status = postgresql.ENUM(
        *COVERAGE_STATUSES,
        name="usage_coverage_status",
        create_type=False,
    )
    postgresql.ENUM(*METRIC_STATUSES, name="usage_metric_status").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*COVERAGE_STATUSES, name="usage_coverage_status").create(
        bind, checkfirst=True
    )

    op.alter_column("daily_usages", "total_usage_minutes", nullable=True)
    op.alter_column("daily_usages", "night_usage_minutes", nullable=True)
    op.add_column(
        "daily_usages",
        sa.Column(
            "total_usage_status",
            metric_status,
            server_default="MEASURED",
            nullable=False,
        ),
    )
    for column in (
        "session_count_status",
        "night_usage_status",
        "max_session_status",
        "unlock_count_status",
    ):
        op.add_column(
            "daily_usages",
            sa.Column(column, metric_status, server_default="UNAVAILABLE", nullable=False),
        )
    op.execute(
        "UPDATE daily_usages SET session_count_status = 'MEASURED' "
        "WHERE session_count IS NOT NULL"
    )
    op.execute(
        "UPDATE daily_usages SET night_usage_status = 'MEASURED' "
        "WHERE night_usage_minutes IS NOT NULL"
    )
    op.execute(
        "UPDATE daily_usages SET max_session_status = 'MEASURED' "
        "WHERE max_session_minutes IS NOT NULL"
    )
    op.execute(
        "UPDATE daily_usages SET unlock_count_status = 'MEASURED' "
        "WHERE unlock_count IS NOT NULL"
    )
    op.add_column(
        "daily_usages",
        sa.Column(
            "applications_status",
            metric_status,
            server_default="MEASURED",
            nullable=False,
        ),
    )
    op.add_column(
        "daily_usages",
        sa.Column("applications_present", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "daily_usages",
        sa.Column(
            "coverage_status",
            coverage_status,
            server_default="UNKNOWN",
            nullable=False,
        ),
    )
    op.add_column("daily_usages", sa.Column("period_start_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("daily_usages", sa.Column("period_end_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("daily_usages", sa.Column("timezone_name", sa.String(64), nullable=True))
    op.add_column(
        "daily_usages",
        sa.Column("duration_unit", sa.String(16), server_default="MINUTES", nullable=False),
    )
    op.add_column("daily_usages", sa.Column("measurement_method", sa.String(80), nullable=True))
    op.add_column("daily_usages", sa.Column("calculation_version", sa.String(80), nullable=True))
    op.add_column("daily_usages", sa.Column("night_window_start", sa.Time(), nullable=True))
    op.add_column("daily_usages", sa.Column("night_window_end", sa.Time(), nullable=True))
    op.add_column(
        "daily_usages",
        sa.Column("periods_uniform", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "daily_usages",
        sa.Column("source_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "daily_usages",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )

    op.create_table(
        "usage_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_name", sa.String(64), nullable=False),
        sa.Column("duration_unit", sa.String(16), nullable=False),
        sa.Column("measurement_method", sa.String(80), nullable=False),
        sa.Column("calculation_version", sa.String(80), nullable=False),
        sa.Column("coverage_status", coverage_status, nullable=False),
        sa.Column("total_usage_minutes", sa.Integer(), nullable=True),
        sa.Column("session_count", sa.Integer(), nullable=True),
        sa.Column("night_usage_minutes", sa.Integer(), nullable=True),
        sa.Column("max_session_minutes", sa.Integer(), nullable=True),
        sa.Column("unlock_count", sa.Integer(), nullable=True),
        sa.Column("total_usage_status", metric_status, nullable=False),
        sa.Column("session_count_status", metric_status, nullable=False),
        sa.Column("night_usage_status", metric_status, nullable=False),
        sa.Column("max_session_status", metric_status, nullable=False),
        sa.Column("unlock_count_status", metric_status, nullable=False),
        sa.Column("applications_status", metric_status, nullable=False),
        sa.Column("applications_present", sa.Boolean(), nullable=False),
        sa.Column("night_window_start", sa.Time(), nullable=True),
        sa.Column("night_window_end", sa.Time(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_snapshots_profile_id", "usage_snapshots", ["profile_id"])
    op.create_index("ix_usage_snapshots_device_id", "usage_snapshots", ["device_id"])
    op.create_index("ix_usage_snapshots_date", "usage_snapshots", ["date"])
    op.create_index("ix_usage_snapshots_profile_date", "usage_snapshots", ["profile_id", "date"])
    op.create_index(
        "ix_usage_snapshots_device_date_captured",
        "usage_snapshots",
        ["device_id", "date", "captured_at"],
    )

    op.create_table(
        "snapshot_app_usages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("package_name", sa.String(255), nullable=False),
        sa.Column("app_name", sa.String(150), nullable=True),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("usage_minutes", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["usage_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "package_name",
            name="uq_snapshot_app_usages_snapshot_package",
        ),
    )
    op.create_index(
        "ix_snapshot_app_usages_snapshot_id",
        "snapshot_app_usages",
        ["snapshot_id"],
    )

    op.create_table(
        "device_daily_usage_heads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["usage_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "device_id",
            "date",
            name="uq_device_daily_usage_heads_device_date",
        ),
    )
    op.create_index(
        "ix_device_daily_usage_heads_profile_id",
        "device_daily_usage_heads",
        ["profile_id"],
    )
    op.create_index(
        "ix_device_daily_usage_heads_device_id",
        "device_daily_usage_heads",
        ["device_id"],
    )
    op.create_index(
        "ix_device_daily_usage_heads_date",
        "device_daily_usage_heads",
        ["date"],
    )


def downgrade():
    op.drop_table("device_daily_usage_heads")
    op.drop_table("snapshot_app_usages")
    op.drop_table("usage_snapshots")
    for column in (
        "updated_at",
        "source_count",
        "periods_uniform",
        "night_window_end",
        "night_window_start",
        "calculation_version",
        "measurement_method",
        "duration_unit",
        "timezone_name",
        "period_end_at",
        "period_start_at",
        "coverage_status",
        "applications_present",
        "applications_status",
        "unlock_count_status",
        "max_session_status",
        "night_usage_status",
        "session_count_status",
        "total_usage_status",
    ):
        op.drop_column("daily_usages", column)
    op.execute("UPDATE daily_usages SET total_usage_minutes = 0 WHERE total_usage_minutes IS NULL")
    op.execute("UPDATE daily_usages SET night_usage_minutes = 0 WHERE night_usage_minutes IS NULL")
    op.alter_column("daily_usages", "night_usage_minutes", nullable=False)
    op.alter_column("daily_usages", "total_usage_minutes", nullable=False)
    bind = op.get_bind()
    postgresql.ENUM(name="usage_coverage_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="usage_metric_status").drop(bind, checkfirst=True)
