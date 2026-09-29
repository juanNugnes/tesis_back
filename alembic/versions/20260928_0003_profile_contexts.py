"""Add profile contexts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260928_0003"
down_revision = "20260818_0002"
branch_labels = None
depends_on = None


def upgrade():
    context_type = postgresql.ENUM(
        "VACATION",
        "ILLNESS",
        "SCHOOL_ACTIVITY",
        "ROUTINE_CHANGE",
        "OTHER",
        name="profile_context_type",
        create_type=False,
    )
    context_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "profile_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("type", context_type, nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_profile_contexts_profile_start_date",
        "profile_contexts",
        ["profile_id", "start_date"],
    )


def downgrade():
    op.drop_index("ix_profile_contexts_profile_start_date", table_name="profile_contexts")
    op.drop_table("profile_contexts")
    postgresql.ENUM(name="profile_context_type").drop(op.get_bind(), checkfirst=True)
