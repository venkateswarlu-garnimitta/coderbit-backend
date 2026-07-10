import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.metric import Metric
from ..repositories.metric_repository import metric_repository

logger = logging.getLogger(__name__)

_SEED_FILE = Path(__file__).resolve().parents[2] / "seeds" / "default_metrics.json"


def _load_default_metrics() -> list[dict[str, Any]]:
    if not _SEED_FILE.is_file():
        logger.warning("Metric seed file not found: %s", _SEED_FILE)
        return []

    with _SEED_FILE.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, list):
        logger.warning("Metric seed file must be a JSON array: %s", _SEED_FILE)
        return []

    return [item for item in payload if isinstance(item, dict)]


async def seed_metrics(db: AsyncSession) -> tuple[int, int, int]:
    """Insert or refresh default metrics. Returns (created, updated, skipped)."""
    definitions = _load_default_metrics()
    if not definitions:
        return 0, 0, 0

    created = 0
    updated = 0
    skipped = 0

    for definition in definitions:
        key = str(definition.get("key", "")).strip()
        name = str(definition.get("name", "")).strip()
        rubric = str(definition.get("rubric", "")).strip()
        metric_type = definition.get("metric_type")
        if metric_type is not None:
            metric_type = str(metric_type).strip() or None
        if not key or not name or not rubric:
            continue

        existing = await metric_repository.get_by_key(db, key)
        if existing is None:
            metric = Metric(
                id=str(uuid4()),
                key=key,
                name=name,
                rubric=rubric,
                metric_type=metric_type,
                is_custom=False,
                created_at=datetime.now(timezone.utc),
            )
            await metric_repository.create(db, metric)
            created += 1
            continue

        changes: dict[str, Any] = {}
        if existing.name != name:
            changes["name"] = name
        if existing.rubric != rubric:
            changes["rubric"] = rubric
        if existing.metric_type != metric_type:
            changes["metric_type"] = metric_type
        if existing.is_custom:
            changes["is_custom"] = False

        if changes:
            await metric_repository.update(db, existing, changes)
            updated += 1
        else:
            skipped += 1

    if created or updated or skipped:
        logger.info(
            "Metric seed complete: created=%s updated=%s skipped=%s",
            created,
            updated,
            skipped,
        )

    return created, updated, skipped


async def default_metric_ids(db: AsyncSession) -> list[str]:
    """Return IDs for all default (non-custom) metrics."""
    metrics = await metric_repository.list_defaults(db)
    return [metric.id for metric in metrics]
