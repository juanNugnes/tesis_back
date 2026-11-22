"""Link an F12 preparation to its historical submission."""

from alembic import op
import sqlalchemy as sa


revision = "20261122_0021"
down_revision = "20261121_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "f12_evidence_preparations",
        sa.Column("historical_submission_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "f12_evidence_preparations",
        sa.Column("submission_linked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_f12_evidence_preparations_historical_submission_id",
        "f12_evidence_preparations",
        "historical_submissions",
        ["historical_submission_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_f12_evidence_preparations_historical_submission_id",
        "f12_evidence_preparations",
        ["historical_submission_id"],
    )
    op.create_index(
        "ix_f12_evidence_preparations_historical_submission_id",
        "f12_evidence_preparations",
        ["historical_submission_id"],
    )


def downgrade():
    op.drop_index(
        "ix_f12_evidence_preparations_historical_submission_id",
        table_name="f12_evidence_preparations",
    )
    op.drop_constraint(
        "uq_f12_evidence_preparations_historical_submission_id",
        "f12_evidence_preparations",
        type_="unique",
    )
    op.drop_constraint(
        "fk_f12_evidence_preparations_historical_submission_id",
        "f12_evidence_preparations",
        type_="foreignkey",
    )
    op.drop_column("f12_evidence_preparations", "submission_linked_at")
    op.drop_column("f12_evidence_preparations", "historical_submission_id")
