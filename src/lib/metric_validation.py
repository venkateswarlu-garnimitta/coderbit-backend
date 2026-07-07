import re
from difflib import SequenceMatcher

from ..models.metric import Metric

SIMILARITY_THRESHOLD = 0.85


def normalize_metric_name(name: str) -> str:
    """Return a canonical form used for exact duplicate detection.

    Strips whitespace, lowercases, removes non-alphanumeric characters, and
    collapses repeated spaces.
    """
    normalized = name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def to_metric_key(name: str) -> str:
    """Convert a human-readable metric name to a snake_case key."""
    normalized = name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def metric_name_similarity(a: str, b: str) -> float:
    """Return a 0.0–1.0 similarity score between two metric names."""
    return SequenceMatcher(None, normalize_metric_name(a), normalize_metric_name(b)).ratio()


def find_similar_metric(name: str, metrics: list[Metric], threshold: float = SIMILARITY_THRESHOLD) -> Metric | None:
    """Return an existing metric that is too similar to the proposed name."""
    for metric in metrics:
        if metric_name_similarity(name, metric.name) >= threshold:
            return metric
    return None
