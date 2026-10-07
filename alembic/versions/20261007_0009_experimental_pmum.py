"""Add versioned parental responses and experimental PMUM estimates."""

from alembic import op
import sqlalchemy as sa

revision = "20261007_0009"
down_revision = "20261005_0008"
branch_labels = None
depends_on = None

parental_respondent = sa.Enum(
    "PARENT_OR_CAREGIVER",
    name="parental_respondent",
)
estimate_status = sa.Enum(
    "ESTIMATED",
    "UNAVAILABLE",
    "TECHNICAL_ERROR",
    name="experimental_pmum_estimate_status",
)


def upgrade():
    op.create_table(
        "parental_screen_time_responses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("adult_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("sex_code", sa.Integer(), nullable=True),
        sa.Column("weekday_category", sa.Integer(), nullable=True),
        sa.Column("weekend_category", sa.Integer(), nullable=True),
        sa.Column("respondent", parental_respondent, nullable=False),
        sa.Column("reference_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["adult_id"], ["adult_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "revision",
            name="uq_parental_screen_time_responses_profile_revision",
        ),
    )
    op.create_table(
        "experimental_pmum_estimates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parental_response_id", sa.Uuid(), nullable=False),
        sa.Column("status", estimate_status, nullable=False),
        sa.Column("estimated_pmum_sf_mean", sa.Float(), nullable=True),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("estimated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("technical_error_code", sa.String(length=50), nullable=True),
        sa.Column("technical_error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parental_response_id"],
            ["parental_screen_time_responses.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experimental_pmum_estimates_parental_response_id",
        "experimental_pmum_estimates",
        ["parental_response_id"],
    )
    op.create_index(
        "ix_experimental_pmum_estimates_estimated_at",
        "experimental_pmum_estimates",
        ["estimated_at"],
    )


def downgrade():
    op.drop_index(
        "ix_experimental_pmum_estimates_estimated_at",
        table_name="experimental_pmum_estimates",
    )
    op.drop_index(
        "ix_experimental_pmum_estimates_parental_response_id",
        table_name="experimental_pmum_estimates",
    )
    op.drop_table("experimental_pmum_estimates")
    op.drop_table("parental_screen_time_responses")

    bind = op.get_bind()
    estimate_status.drop(bind, checkfirst=True)
    parental_respondent.drop(bind, checkfirst=True)
