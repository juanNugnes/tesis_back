"""Add profile activity preferences."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260928_0004"
down_revision = "20260928_0003"
branch_labels = None
depends_on = None


def upgrade():
    adaptation_type = postgresql.ENUM(
        "NONE",
        "TEMPORARY",
        "PERMANENT",
        name="profile_adaptation_type",
        create_type=False,
    )
    adaptation_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "profile_activity_preferences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("interests", sa.JSON(), nullable=False),
        sa.Column("adaptation_type", adaptation_type, nullable=False),
        sa.Column("considerations", sa.JSON(), nullable=False),
        sa.Column("activities_to_avoid", sa.JSON(), nullable=False),
        sa.Column("additional_notes", sa.String(length=500), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id"),
    )


def downgrade():
    op.drop_table("profile_activity_preferences")
    postgresql.ENUM(name="profile_adaptation_type").drop(op.get_bind(), checkfirst=True)
