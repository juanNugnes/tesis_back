"""Add explicit historical adult declarations for weekly evaluations."""

from alembic import op
import sqlalchemy as sa


revision = "20261126_0024"
down_revision = "20261125_0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "adult_weekly_declarations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_adult_id",
            sa.Uuid(),
            sa.ForeignKey("adult_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("assignment_verified", sa.Boolean(), nullable=False),
        sa.Column("shared_phone", sa.Boolean(), nullable=False),
        sa.Column("context_declared", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_effective_date", sa.Date(), nullable=True),
        sa.Column(
            "revoked_by_adult_id",
            sa.Uuid(),
            sa.ForeignKey("adult_accounts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="revision_positive",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_range",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_effective_date IS NULL "
            "AND revoked_by_adult_id IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_effective_date IS NOT NULL "
            "AND revoked_by_adult_id IS NOT NULL)",
            name="revocation_complete",
        ),
        sa.UniqueConstraint(
            "profile_id",
            "revision",
            name="uq_adult_weekly_declarations_profile_revision",
        ),
    )
    op.create_index(
        "ix_adult_weekly_declarations_profile_id",
        "adult_weekly_declarations",
        ["profile_id"],
    )
    op.create_index(
        "ix_adult_weekly_declarations_device_id",
        "adult_weekly_declarations",
        ["device_id"],
    )
    op.create_index(
        "ix_adult_weekly_declarations_created_by_adult_id",
        "adult_weekly_declarations",
        ["created_by_adult_id"],
    )
    op.create_index(
        "ix_adult_weekly_declarations_revoked_by_adult_id",
        "adult_weekly_declarations",
        ["revoked_by_adult_id"],
    )
    op.create_index(
        "ix_adult_weekly_declarations_profile_period",
        "adult_weekly_declarations",
        ["profile_id", "effective_from", "effective_to"],
    )
    op.create_index(
        "ix_adult_weekly_declarations_device_period",
        "adult_weekly_declarations",
        ["device_id", "effective_from"],
    )


def downgrade():
    op.drop_index(
        "ix_adult_weekly_declarations_device_period",
        table_name="adult_weekly_declarations",
    )
    op.drop_index(
        "ix_adult_weekly_declarations_profile_period",
        table_name="adult_weekly_declarations",
    )
    op.drop_index(
        "ix_adult_weekly_declarations_revoked_by_adult_id",
        table_name="adult_weekly_declarations",
    )
    op.drop_index(
        "ix_adult_weekly_declarations_created_by_adult_id",
        table_name="adult_weekly_declarations",
    )
    op.drop_index(
        "ix_adult_weekly_declarations_device_id",
        table_name="adult_weekly_declarations",
    )
    op.drop_index(
        "ix_adult_weekly_declarations_profile_id",
        table_name="adult_weekly_declarations",
    )
    op.drop_table("adult_weekly_declarations")
