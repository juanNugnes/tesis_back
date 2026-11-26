"""Allow durable real-verified adult-message evaluation jobs."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261128_0026"
down_revision = "20261127_0025"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "adult_message_evaluation_jobs",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="FICTIONAL",
        ),
    )
    op.add_column(
        "adult_message_evaluation_jobs",
        sa.Column("window_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "adult_message_evaluation_jobs",
        sa.Column(
            "traceability",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.alter_column(
        "adult_message_evaluation_jobs",
        "fixture_id",
        existing_type=sa.String(length=128),
        nullable=True,
    )
    op.alter_column(
        "adult_message_evaluation_jobs",
        "fixture_sha256",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.create_unique_constraint(
        "uq_adult_message_evaluation_jobs_window_fingerprint",
        "adult_message_evaluation_jobs",
        ["window_fingerprint"],
    )
    op.create_check_constraint(
        "ck_adult_message_evaluation_jobs_source_metadata",
        "adult_message_evaluation_jobs",
        "(source = 'FICTIONAL' AND fixture_id IS NOT NULL "
        "AND fixture_sha256 IS NOT NULL AND window_fingerprint IS NULL) OR "
        "(source = 'REAL_VERIFIED' AND fixture_id IS NULL "
        "AND fixture_sha256 IS NULL AND window_fingerprint IS NOT NULL "
        "AND traceability IS NOT NULL)",
    )


def downgrade():
    op.drop_constraint(
        "ck_adult_message_evaluation_jobs_source_metadata",
        "adult_message_evaluation_jobs",
        type_="check",
    )
    op.drop_constraint(
        "uq_adult_message_evaluation_jobs_window_fingerprint",
        "adult_message_evaluation_jobs",
        type_="unique",
    )
    op.execute(
        "DELETE FROM adult_message_evaluation_jobs WHERE source = 'REAL_VERIFIED'"
    )
    op.alter_column(
        "adult_message_evaluation_jobs",
        "fixture_sha256",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "adult_message_evaluation_jobs",
        "fixture_id",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.drop_column("adult_message_evaluation_jobs", "traceability")
    op.drop_column("adult_message_evaluation_jobs", "window_fingerprint")
    op.drop_column("adult_message_evaluation_jobs", "source")
