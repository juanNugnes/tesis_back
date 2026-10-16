"""Add device deactivation audit event."""

from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20261017_0014"
down_revision = "20261016_0013"
branch_labels = None
depends_on = None


OLD_EVENT_TYPES = (
    "REPRESENTATIVE_VERIFICATION_APPROVED",
    "REPRESENTATIVE_VERIFICATION_REJECTED",
    "CONSENT_GRANTED",
    "CONSENT_REVOKED",
)


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'DEVICE_DEACTIVATED'"
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DELETE FROM audit_events WHERE event_type = 'DEVICE_DEACTIVATED'")
    op.execute("ALTER TYPE audit_event_type RENAME TO audit_event_type_old")
    postgresql.ENUM(*OLD_EVENT_TYPES, name="audit_event_type").create(bind)
    op.execute(
        "ALTER TABLE audit_events ALTER COLUMN event_type TYPE audit_event_type "
        "USING event_type::text::audit_event_type"
    )
    op.execute("DROP TYPE audit_event_type_old")
