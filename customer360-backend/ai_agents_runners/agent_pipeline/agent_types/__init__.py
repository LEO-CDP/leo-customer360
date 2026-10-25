"""Model-type-specific strategy implementations and output contracts."""

from .anomaly_detection import AnomalyDetectionPipeline
from .classification import ClassificationPipeline
from .clustering import ClusteringPipeline
from .forecasting import ForecastingPipeline
from .generative_llm import GenerativeLlmPipeline
from .optimization import OptimizationPipeline
from .ranking_recommendation import RankingRecommendationPipeline
from .regression import RegressionPipeline
from .rules_engine import RulesEnginePipeline
from .uplift_modeling import UpliftModelingPipeline

__all__ = [
    "AnomalyDetectionPipeline",
    "ClassificationPipeline",
    "ClusteringPipeline",
    "ForecastingPipeline",
    "GenerativeLlmPipeline",
    "OptimizationPipeline",
    "RankingRecommendationPipeline",
    "RegressionPipeline",
    "RulesEnginePipeline",
    "UpliftModelingPipeline",
]
