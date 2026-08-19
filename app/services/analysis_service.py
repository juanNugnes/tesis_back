from app.db.models import AnalysisResult, DailyUsage, RiskLevel
from app.repositories.analysis_repository import AnalysisRepository
from app.services.baseline_service import BaselineService
from app.services.ml_service import MLService, MockMLService
from app.services.rules_service import RulesService


class AnalysisService:
    def __init__(self, repository: AnalysisRepository, baseline_service: BaselineService | None = None, rules_service: RulesService | None = None, ml_service: MLService | None = None):
        self.repository = repository
        self.baseline_service = baseline_service or BaselineService()
        self.rules_service = rules_service or RulesService()
        self.ml_service = ml_service or MockMLService()

    async def analyze(self, usage: DailyUsage) -> AnalysisResult:
        baseline = await self.baseline_service.get_baseline(usage.profile_id)
        features = {"total_usage_minutes": usage.total_usage_minutes, "night_usage_minutes": usage.night_usage_minutes, "session_count": usage.session_count, "max_session_minutes": usage.max_session_minutes, "unlock_count": usage.unlock_count}
        rules = self.rules_service.evaluate(features, baseline)
        prediction = self.ml_service.predict(features)
        risk = RiskLevel.ATTENTION if rules else RiskLevel.NORMAL
        result = AnalysisResult(profile_id=usage.profile_id, daily_usage_id=usage.id, anomaly_detected=prediction.anomaly_detected, anomaly_score=prediction.anomaly_score, risk_level=risk, explanation="; ".join(rules) or "No behavioral changes detected by the current mock analysis.", model_version=prediction.model_version)
        return await self.repository.create(result)

