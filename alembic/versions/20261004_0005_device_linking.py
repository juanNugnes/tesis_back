"""Add device identity and profile link codes."""

from alembic import op
import sqlalchemy as sa

revision = "20261004_0005"
down_revision = "20260928_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("installation_id", name="uq_devices_installation_id"),
    )
    op.create_index("ix_devices_profile_id", "devices", ["profile_id"])

    op.create_table(
        "profile_link_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_profile_link_codes_code_hash"),
    )
    op.create_index("ix_profile_link_codes_profile_id", "profile_link_codes", ["profile_id"])
    op.create_index("ix_profile_link_codes_expires_at", "profile_link_codes", ["expires_at"])


def downgrade():
    op.drop_index("ix_profile_link_codes_expires_at", table_name="profile_link_codes")
    op.drop_index("ix_profile_link_codes_profile_id", table_name="profile_link_codes")
    op.drop_table("profile_link_codes")
    op.drop_index("ix_devices_profile_id", table_name="devices")
    op.drop_table("devices")
