"""Preserve the local time zone used by weekly adult declarations."""

from alembic import op
import sqlalchemy as sa


revision = "20261127_0025"
down_revision = "20261126_0024"
branch_labels = None
depends_on = None


def upgrade():
    # Existing rows cannot be backfilled safely: no profile-level historical
    # time-zone source existed when they were created. New writes require it.
    op.add_column(
        "adult_weekly_declarations",
        sa.Column("time_zone", sa.String(length=128), nullable=True),
    )


def downgrade():
    op.drop_column("adult_weekly_declarations", "time_zone")
