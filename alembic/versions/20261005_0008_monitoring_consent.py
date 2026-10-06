"""Add representative verification, consent, and audit events."""

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "20261005_0008"
down_revision = "20261005_0007"
branch_labels = None
depends_on = None

verification_status = sa.Enum(
    "PENDING",
    "APPROVED",
    "REJECTED",
    name="representative_verification_status",
)
consent_status = sa.Enum("ACTIVE", "REVOKED", name="consent_status")
consent_purpose = sa.Enum("USAGE_MONITORING", name="consent_purpose_type")
audit_actor_type = sa.Enum("ADULT", "INTERNAL", name="audit_actor_type")
audit_event_type = sa.Enum(
    "REPRESENTATIVE_VERIFICATION_APPROVED",
    "REPRESENTATIVE_VERIFICATION_REJECTED",
    "CONSENT_GRANTED",
    "CONSENT_REVOKED",
    name="audit_event_type",
)
audit_result = sa.Enum("SUCCESS", name="audit_result")


def upgrade():
    op.create_table(
        "representative_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("adult_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("status", verification_status, nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["adult_id"], ["adult_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "adult_id",
            "profile_id",
            name="uq_representative_verifications_adult_profile",
        ),
    )
    op.create_index(
        "ix_representative_verifications_adult_id",
        "representative_verifications",
        ["adult_id"],
    )
    op.create_index(
        "ix_representative_verifications_profile_id",
        "representative_verifications",
        ["profile_id"],
    )
    bind = op.get_bind()
    existing_links = list(
        bind.execute(sa.text("SELECT adult_id, profile_id FROM adult_profile_links")).mappings()
    )
    if existing_links:
        now = datetime.now(timezone.utc)
        verification_table = sa.table(
            "representative_verifications",
            sa.column("id", sa.Uuid()),
            sa.column("adult_id", sa.Uuid()),
            sa.column("profile_id", sa.Uuid()),
            sa.column("status", verification_status),
            sa.column("verified_at", sa.DateTime(timezone=True)),
            sa.column("verified_by", sa.String(100)),
            sa.column("notes", sa.String(500)),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(
            verification_table,
            [
                {
                    "id": uuid.uuid4(),
                    "adult_id": link["adult_id"],
                    "profile_id": link["profile_id"],
                    "status": "PENDING",
                    "verified_at": None,
                    "verified_by": None,
                    "notes": None,
                    "created_at": now,
                    "updated_at": now,
                }
                for link in existing_links
            ],
        )

    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("adult_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("status", consent_status, nullable=False),
        sa.Column("privacy_notice_version", sa.String(length=50), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["adult_id"], ["adult_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_consents_adult_id", "consents", ["adult_id"])
    op.create_index("ix_consents_profile_id", "consents", ["profile_id"])
    op.create_index("ix_consents_status", "consents", ["status"])
    op.create_index("ix_consents_revoked_at", "consents", ["revoked_at"])
    op.create_index(
        "uq_consents_active_adult_profile",
        "consents",
        ["adult_id", "profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "consent_purposes",
        sa.Column("consent_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", consent_purpose, nullable=False),
        sa.ForeignKeyConstraint(["consent_id"], ["consents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("consent_id", "purpose"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", audit_actor_type, nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("profile_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", audit_event_type, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", audit_result, nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_profile_id", "audit_events", ["profile_id"])
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])


def downgrade():
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_profile_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("consent_purposes")
    op.drop_index("uq_consents_active_adult_profile", table_name="consents")
    op.drop_index("ix_consents_revoked_at", table_name="consents")
    op.drop_index("ix_consents_status", table_name="consents")
    op.drop_index("ix_consents_profile_id", table_name="consents")
    op.drop_index("ix_consents_adult_id", table_name="consents")
    op.drop_table("consents")
    op.drop_index(
        "ix_representative_verifications_profile_id",
        table_name="representative_verifications",
    )
    op.drop_index(
        "ix_representative_verifications_adult_id",
        table_name="representative_verifications",
    )
    op.drop_table("representative_verifications")

    bind = op.get_bind()
    audit_result.drop(bind, checkfirst=True)
    audit_event_type.drop(bind, checkfirst=True)
    audit_actor_type.drop(bind, checkfirst=True)
    consent_purpose.drop(bind, checkfirst=True)
    consent_status.drop(bind, checkfirst=True)
    verification_status.drop(bind, checkfirst=True)
