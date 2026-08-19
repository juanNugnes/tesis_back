# Machine learning boundary

`MockMLService` currently returns a neutral prediction. A future trained model can implement the same `MLService` protocol and be injected into `AnalysisService`; API routes and persistence do not need to change. No health or psychological diagnosis should be inferred from these results.
