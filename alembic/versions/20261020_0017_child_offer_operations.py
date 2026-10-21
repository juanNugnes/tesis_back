"""Persist automatic child-offer transport operations separately from voluntary offers."""

from alembic import op
import sqlalchemy as sa


revision = "20261020_0017"
down_revision = "20261019_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "child_offer_operations",
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_child_offer_operations_profile_status", "child_offer_operations", ["profile_id", "status"])


def downgrade():
    op.drop_index("ix_child_offer_operations_profile_status", table_name="child_offer_operations")
    op.drop_table("child_offer_operations")
