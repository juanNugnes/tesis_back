"""Add adult authentication and profile authorization."""

from alembic import op
import sqlalchemy as sa

revision = "20261005_0006"
down_revision = "20261004_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "adult_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_adult_accounts_email"),
    )
    op.create_table(
        "adult_profile_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("adult_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["adult_id"], ["adult_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("adult_id", "profile_id", name="uq_adult_profile_links_adult_profile"),
    )
    op.create_index("ix_adult_profile_links_adult_id", "adult_profile_links", ["adult_id"])
    op.create_index("ix_adult_profile_links_profile_id", "adult_profile_links", ["profile_id"])
    op.create_table(
        "adult_otp_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("adult_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["adult_id"], ["adult_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_adult_otp_challenges_adult_id", "adult_otp_challenges", ["adult_id"])
    op.create_index("ix_adult_otp_challenges_expires_at", "adult_otp_challenges", ["expires_at"])


def downgrade():
    op.drop_index("ix_adult_otp_challenges_expires_at", table_name="adult_otp_challenges")
    op.drop_index("ix_adult_otp_challenges_adult_id", table_name="adult_otp_challenges")
    op.drop_table("adult_otp_challenges")
    op.drop_index("ix_adult_profile_links_profile_id", table_name="adult_profile_links")
    op.drop_index("ix_adult_profile_links_adult_id", table_name="adult_profile_links")
    op.drop_table("adult_profile_links")
    op.drop_table("adult_accounts")
