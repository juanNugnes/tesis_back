"""Add the last observed nighttime application activity closure."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261018_0015"
down_revision = "20261017_0014"
branch_labels = None
depends_on = None


METRIC_STATUSES = (
    "MEASURED",
    "UNAVAILABLE",
    "PERMISSION_DENIED",
    "CAPTURE_ERROR",
    "INCOMPLETE",
)


def upgrade():
    metric_status = postgresql.ENUM(
        *METRIC_STATUSES,
        name="usage_metric_status",
        create_type=False,
    )
    for table in ("daily_usages", "usage_snapshots"):
        op.add_column(
            table,
            sa.Column(
                "last_night_activity_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "last_night_activity_status",
                metric_status,
                server_default="UNAVAILABLE",
                nullable=False,
            ),
        )


def downgrade():
    for table in ("usage_snapshots", "daily_usages"):
        op.drop_column(table, "last_night_activity_status")
        op.drop_column(table, "last_night_activity_at")
