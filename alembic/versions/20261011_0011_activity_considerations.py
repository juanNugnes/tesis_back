"""Add per-item profile activity considerations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261011_0011"
down_revision = "20261009_0010"
branch_labels = None
depends_on = None


def upgrade():
    consideration_type = postgresql.ENUM(
        "UPPER_BODY_MOBILITY",
        "LOWER_BODY_MOBILITY",
        "VISUAL",
        "AUDITORY",
        "SOUND_SENSITIVITY",
        name="activity_consideration_type",
        create_type=False,
    )
    duration_type = postgresql.ENUM(
        "TEMPORARY",
        "PERMANENT",
        name="consideration_duration_type",
        create_type=False,
    )
    consideration_type.create(op.get_bind(), checkfirst=True)
    duration_type.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "profile_activity_preferences",
        sa.Column(
            "considerations_answered",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_table(
        "profile_activity_considerations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("preferences_id", sa.Uuid(), nullable=False),
        sa.Column("type", consideration_type, nullable=False),
        sa.Column("duration_type", duration_type, nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(duration_type = 'TEMPORARY' AND valid_until IS NOT NULL) OR "
            "(duration_type = 'PERMANENT' AND valid_until IS NULL)",
            name="ck_profile_activity_considerations_duration_valid_until",
        ),
        sa.ForeignKeyConstraint(
            ["preferences_id"],
            ["profile_activity_preferences.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "preferences_id",
            "ordinal",
            name="uq_profile_activity_considerations_preferences_ordinal",
        ),
        sa.UniqueConstraint(
            "preferences_id",
            "type",
            name="uq_profile_activity_considerations_preferences_type",
        ),
    )
    op.create_index(
        "ix_profile_activity_considerations_preferences_id",
        "profile_activity_considerations",
        ["preferences_id"],
    )


def downgrade():
    op.drop_index(
        "ix_profile_activity_considerations_preferences_id",
        table_name="profile_activity_considerations",
    )
    op.drop_table("profile_activity_considerations")
    op.drop_column("profile_activity_preferences", "considerations_answered")
    postgresql.ENUM(name="consideration_duration_type").drop(
        op.get_bind(),
        checkfirst=True,
    )
    postgresql.ENUM(name="activity_consideration_type").drop(
        op.get_bind(),
        checkfirst=True,
    )
