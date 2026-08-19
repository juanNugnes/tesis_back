"""Initial schema."""
from alembic import op
import sqlalchemy as sa

revision = "20260815_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    risk = sa.Enum("NORMAL", "ATTENTION", "RELEVANT_CHANGE", name="risk_level")
    op.create_table("profiles", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("alias", sa.String(100), nullable=False), sa.Column("age", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("active", sa.Boolean(), nullable=False))
    op.create_table("daily_usages", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False), sa.Column("date", sa.Date(), nullable=False), sa.Column("total_usage_minutes", sa.Integer(), nullable=False), sa.Column("session_count", sa.Integer()), sa.Column("night_usage_minutes", sa.Integer(), nullable=False), sa.Column("max_session_minutes", sa.Integer()), sa.Column("unlock_count", sa.Integer()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("profile_id", "date", name="uq_daily_usages_profile_date"))
    op.create_index("ix_daily_usages_profile_id", "daily_usages", ["profile_id"]); op.create_index("ix_daily_usages_date", "daily_usages", ["date"]); op.create_index("ix_daily_usages_profile_date", "daily_usages", ["profile_id", "date"])
    op.create_table("app_usages", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("daily_usage_id", sa.Uuid(), sa.ForeignKey("daily_usages.id", ondelete="CASCADE"), nullable=False), sa.Column("package_name", sa.String(255), nullable=False), sa.Column("app_name", sa.String(150)), sa.Column("category", sa.String(80)), sa.Column("usage_minutes", sa.Integer(), nullable=False)); op.create_index("ix_app_usages_daily_usage_id", "app_usages", ["daily_usage_id"])
    op.create_table("analysis_results", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False), sa.Column("daily_usage_id", sa.Uuid(), sa.ForeignKey("daily_usages.id", ondelete="CASCADE"), nullable=False), sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("anomaly_detected", sa.Boolean(), nullable=False), sa.Column("anomaly_score", sa.Float()), sa.Column("risk_level", risk, nullable=False), sa.Column("explanation", sa.Text()), sa.Column("model_version", sa.String(80))); op.create_index("ix_analysis_results_profile_id", "analysis_results", ["profile_id"]); op.create_index("ix_analysis_results_daily_usage_id", "analysis_results", ["daily_usage_id"])
    op.create_table("alerts", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False), sa.Column("analysis_result_id", sa.Uuid(), sa.ForeignKey("analysis_results.id", ondelete="CASCADE"), nullable=False), sa.Column("title", sa.String(150), nullable=False), sa.Column("message", sa.Text(), nullable=False), sa.Column("read", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)); op.create_index("ix_alerts_profile_id", "alerts", ["profile_id"]); op.create_index("ix_alerts_analysis_result_id", "alerts", ["analysis_result_id"])


def downgrade():
    op.drop_table("alerts"); op.drop_table("analysis_results"); op.drop_table("app_usages"); op.drop_table("daily_usages"); op.drop_table("profiles"); sa.Enum(name="risk_level").drop(op.get_bind(), checkfirst=True)
