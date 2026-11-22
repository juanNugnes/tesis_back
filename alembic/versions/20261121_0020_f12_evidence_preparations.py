"""Persist audited F12 preparations without submitting them to Analytics."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261121_0020"
down_revision = "20261120_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "f12_evidence_preparations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("preparer_commit", sa.String(length=40), nullable=False),
        sa.Column("measurement_version", sa.String(length=100), nullable=False),
        sa.Column("bridge_version", sa.String(length=100), nullable=False),
        sa.Column("export_adapter_version", sa.String(length=100), nullable=False),
        sa.Column("tzdata_version", sa.String(length=50), nullable=False),
        sa.Column("series_key", sa.String(length=71), nullable=False),
        sa.Column("representation_record_id", sa.String(length=68), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("projection_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("evidence_content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "submission_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "audit",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["f12_raw_evidence_captures.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id"),
        sa.UniqueConstraint("request_id"),
        sa.UniqueConstraint("representation_record_id"),
    )
    op.create_index(
        "ix_f12_evidence_preparations_evidence_id",
        "f12_evidence_preparations",
        ["evidence_id"],
    )
    op.create_index(
        "ix_f12_evidence_preparations_series_key",
        "f12_evidence_preparations",
        ["series_key"],
    )


def downgrade():
    op.drop_index(
        "ix_f12_evidence_preparations_series_key",
        table_name="f12_evidence_preparations",
    )
    op.drop_index(
        "ix_f12_evidence_preparations_evidence_id",
        table_name="f12_evidence_preparations",
    )
    op.drop_table("f12_evidence_preparations")
