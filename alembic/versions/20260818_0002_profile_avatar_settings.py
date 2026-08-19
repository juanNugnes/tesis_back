"""Add profile avatar and profile settings."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260818_0002"
down_revision = "20260815_0001"
branch_labels = None
depends_on = None


def upgrade():
    avatar_type = postgresql.ENUM(
        "fox", "frog", "dog", "cat", "penguin", "lion", "koala", "butterfly",
        name="avatar_type",
        create_type=False,
    )
    avatar_type.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "profiles",
        sa.Column("avatar", avatar_type, nullable=False, server_default=sa.text("'fox'::avatar_type")),
    )
    op.alter_column("profiles", "avatar", server_default=None)

    op.create_table(
        "profile_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("school_start", sa.Time(), nullable=True),
        sa.Column("school_end", sa.Time(), nullable=True),
        sa.Column("sleep_start", sa.Time(), nullable=True),
        sa.Column("sleep_end", sa.Time(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id"),
    )


def downgrade():
    op.drop_table("profile_settings")
    op.drop_column("profiles", "avatar")
    postgresql.ENUM(name="avatar_type").drop(op.get_bind(), checkfirst=True)
