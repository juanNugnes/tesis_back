"""Add one-time bootstrap tokens for initial device registration."""

from alembic import op
import sqlalchemy as sa

revision = "20261005_0007"
down_revision = "20261005_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "device_bootstrap_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_device_bootstrap_tokens_hash"),
    )
    op.create_index(
        "ix_device_bootstrap_tokens_profile_id",
        "device_bootstrap_tokens",
        ["profile_id"],
    )
    op.create_index(
        "ix_device_bootstrap_tokens_expires_at",
        "device_bootstrap_tokens",
        ["expires_at"],
    )


def downgrade():
    op.drop_index(
        "ix_device_bootstrap_tokens_expires_at",
        table_name="device_bootstrap_tokens",
    )
    op.drop_index(
        "ix_device_bootstrap_tokens_profile_id",
        table_name="device_bootstrap_tokens",
    )
    op.drop_table("device_bootstrap_tokens")
