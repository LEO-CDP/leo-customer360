"""Model-type-specific strategy implementations and output contracts."""

from .anomaly_detection import AnomalyDetectionPipeline
from .classification import ClassificationPipeline
from .clustering import ClusteringPipeline
from .forecasting import ForecastingPipeline
from .generative_llm import GenerativeLlmPipeline
from .graph_ml import GraphIntelligencePipeline
from .optimization import OptimizationPipeline
from .ranking_recommendation import RankingRecommendationPipeline
from .regression import RegressionPipeline
from .rules_engine import RulesEnginePipeline
from .semantic_embedding import SemanticEmbeddingPipeline
from .uplift_modeling import UpliftModelingPipeline

__all__ = [
    "AnomalyDetectionPipeline",
    "ClassificationPipeline",
    "ClusteringPipeline",
    "ForecastingPipeline",
    "GenerativeLlmPipeline",
    "GraphIntelligencePipeline",
    "OptimizationPipeline",
    "RankingRecommendationPipeline",
    "RegressionPipeline",
    "RulesEnginePipeline",
    "SemanticEmbeddingPipeline",
    "UpliftModelingPipeline",
]
