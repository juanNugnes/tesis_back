"""Store adult controls required by adaptive automatic child offers."""

from alembic import op
import sqlalchemy as sa


revision = "20261117_0018"
down_revision = "20261020_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "automatic_offer_settings",
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "adult_id",
            sa.Uuid(),
            sa.ForeignKey("adult_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("automatic_enabled", sa.Boolean(), nullable=False),
        sa.Column("shared_phone", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_automatic_offer_settings_adult_id",
        "automatic_offer_settings",
        ["adult_id"],
    )


def downgrade():
    op.drop_index(
        "ix_automatic_offer_settings_adult_id",
        table_name="automatic_offer_settings",
    )
    op.drop_table("automatic_offer_settings")
