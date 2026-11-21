"""Store immutable original Android F12 evidence."""

from alembic import op
import sqlalchemy as sa


revision = "20261120_0019"
down_revision = "20261117_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "f12_raw_evidence_captures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("capture_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("time_zone", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("envelope_version", sa.Integer(), nullable=False),
        sa.Column("adapter_version", sa.String(length=100), nullable=False),
        sa.Column("evidence_state", sa.String(length=20), nullable=False),
        sa.Column("unavailable_reason", sa.String(length=50), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("pair_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_status", sa.String(length=20), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_f12_raw_evidence_revision_positive",
        ),
        sa.CheckConstraint(
            "pair_count >= 0",
            name="ck_f12_raw_evidence_pair_count_non_negative",
        ),
        sa.CheckConstraint(
            "total_bytes >= 0",
            name="ck_f12_raw_evidence_total_bytes_non_negative",
        ),
        sa.CheckConstraint(
            "evidence_state IN ('AVAILABLE', 'UNAVAILABLE')",
            name="ck_f12_raw_evidence_state",
        ),
        sa.CheckConstraint(
            "receipt_status = 'RECEIVED'",
            name="ck_f12_raw_evidence_receipt_status",
        ),
        sa.CheckConstraint(
            "(evidence_state = 'AVAILABLE' AND unavailable_reason IS NULL AND pair_count > 0) OR "
            "(evidence_state = 'UNAVAILABLE' AND unavailable_reason = 'NOT_EXPORTED' AND pair_count = 0 AND total_bytes = 0)",
            name="ck_f12_raw_evidence_state_content",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capture_id", name="uq_f12_raw_evidence_captures_capture_id"),
        sa.UniqueConstraint(
            "profile_id",
            "device_id",
            "local_date",
            "revision",
            name="uq_f12_raw_evidence_profile_device_date_revision",
        ),
    )
    op.create_index(
        "ix_f12_raw_evidence_captures_profile_id",
        "f12_raw_evidence_captures",
        ["profile_id"],
    )
    op.create_index(
        "ix_f12_raw_evidence_captures_device_id",
        "f12_raw_evidence_captures",
        ["device_id"],
    )
    op.create_index(
        "ix_f12_raw_evidence_profile_date",
        "f12_raw_evidence_captures",
        ["profile_id", "local_date"],
    )
    op.create_index(
        "ix_f12_raw_evidence_expires_at",
        "f12_raw_evidence_captures",
        ["expires_at"],
    )

    op.create_table(
        "f12_raw_evidence_pairs",
        sa.Column("capture_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("collector_run_id", sa.String(length=36), nullable=False),
        sa.Column("summary_sha256", sa.String(length=64), nullable=False),
        sa.Column("events_sha256", sa.String(length=64), nullable=False),
        sa.Column("summary_size", sa.BigInteger(), nullable=False),
        sa.Column("events_size", sa.BigInteger(), nullable=False),
        sa.Column("summary_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("events_bytes", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_f12_raw_evidence_pair_ordinal_non_negative",
        ),
        sa.CheckConstraint(
            "summary_size >= 0",
            name="ck_f12_raw_evidence_summary_size_non_negative",
        ),
        sa.CheckConstraint(
            "events_size >= 0",
            name="ck_f12_raw_evidence_events_size_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"],
            ["f12_raw_evidence_captures.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("capture_id", "ordinal"),
        sa.UniqueConstraint(
            "capture_id",
            "collector_run_id",
            name="uq_f12_raw_evidence_capture_collector_run",
        ),
    )


def downgrade():
    op.drop_table("f12_raw_evidence_pairs")
    op.drop_index(
        "ix_f12_raw_evidence_expires_at",
        table_name="f12_raw_evidence_captures",
    )
    op.drop_index(
        "ix_f12_raw_evidence_profile_date",
        table_name="f12_raw_evidence_captures",
    )
    op.drop_index(
        "ix_f12_raw_evidence_captures_device_id",
        table_name="f12_raw_evidence_captures",
    )
    op.drop_index(
        "ix_f12_raw_evidence_captures_profile_id",
        table_name="f12_raw_evidence_captures",
    )
    op.drop_table("f12_raw_evidence_captures")
