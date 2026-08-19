from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Prediction:
    anomaly_detected: bool
    anomaly_score: float | None
    model_version: str


class MLService(Protocol):
    def predict(self, features: dict[str, Any]) -> Prediction: ...


class MockMLService:
    """Neutral adapter. Replace this class with a persisted scikit-learn model later."""
    def predict(self, features: dict[str, Any]) -> Prediction:
        # TODO: validate the finalized feature vector and invoke the trained model.
        return Prediction(anomaly_detected=False, anomaly_score=None, model_version="mock-v1")

