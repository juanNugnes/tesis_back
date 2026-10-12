"""Add activity offers, offered items, and selections."""

from alembic import op
import sqlalchemy as sa

revision = "20261009_0010"
down_revision = "20261007_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "activity_offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("context", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_activity_offers_profile_id", "activity_offers", ["profile_id"])
    op.create_index("ix_activity_offers_device_id", "activity_offers", ["device_id"])
    op.create_index("ix_activity_offers_created_at", "activity_offers", ["created_at"])

    op.create_table(
        "activity_offer_items",
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["activity_offers.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("offer_id", "ordinal"),
        sa.UniqueConstraint(
            "offer_id",
            "activity_id",
            name="uq_activity_offer_items_offer_activity",
        ),
    )

    op.create_table(
        "activity_selections",
        sa.Column("selection_id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("activity_id", sa.String(length=100), nullable=False),
        sa.Column("selected_at_epoch_millis", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["activity_offers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("selection_id"),
    )
    op.create_index(
        "ix_activity_selections_offer_id",
        "activity_selections",
        ["offer_id"],
    )
    op.create_index(
        "ix_activity_selections_profile_id",
        "activity_selections",
        ["profile_id"],
    )
    op.create_index(
        "ix_activity_selections_device_id",
        "activity_selections",
        ["device_id"],
    )
    op.create_index(
        "ix_activity_selections_created_at",
        "activity_selections",
        ["created_at"],
    )


def downgrade():
    op.drop_index("ix_activity_selections_created_at", table_name="activity_selections")
    op.drop_index("ix_activity_selections_device_id", table_name="activity_selections")
    op.drop_index("ix_activity_selections_profile_id", table_name="activity_selections")
    op.drop_index("ix_activity_selections_offer_id", table_name="activity_selections")
    op.drop_table("activity_selections")
    op.drop_table("activity_offer_items")
    op.drop_index("ix_activity_offers_created_at", table_name="activity_offers")
    op.drop_index("ix_activity_offers_device_id", table_name="activity_offers")
    op.drop_index("ix_activity_offers_profile_id", table_name="activity_offers")
    op.drop_table("activity_offers")
