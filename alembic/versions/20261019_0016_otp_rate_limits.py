"""Persist OTP request and verification rate limit events."""

from alembic import op
import sqlalchemy as sa


revision = "20261019_0016"
down_revision = "20261018_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "otp_rate_limit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_otp_rate_limit_events_lookup",
        "otp_rate_limit_events",
        ["scope", "key_hash", "occurred_at"],
    )
    op.create_index(
        "ix_otp_rate_limit_events_occurred_at",
        "otp_rate_limit_events",
        ["occurred_at"],
    )


def downgrade():
    op.drop_index("ix_otp_rate_limit_events_occurred_at", table_name="otp_rate_limit_events")
    op.drop_index("ix_otp_rate_limit_events_lookup", table_name="otp_rate_limit_events")
    op.drop_table("otp_rate_limit_events")
