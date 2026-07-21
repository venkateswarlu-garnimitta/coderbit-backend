from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db, require_role
from ..lib.metric_validation import (
    find_similar_metric,
    normalize_metric_name,
    to_metric_key,
)
from ..models.metric import Metric
from ..repositories.metric_repository import metric_repository
from ..schemas.metrics import MetricCreate, MetricOut, MetricUpdate

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _format_metric(metric: Metric) -> MetricOut:
    return MetricOut(
        id=metric.id,
        key=metric.key,
        name=metric.name,
        rubric=metric.rubric,
        metric_type=metric.metric_type,
        is_custom=metric.is_custom,
        created_at=metric.created_at.isoformat(),
    )


@router.get("", response_model=list[MetricOut])
async def list_metrics(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin", "interviewer")),
):
    metrics = await metric_repository.list_all(db)
    return [_format_metric(m) for m in metrics]


@router.post("", response_model=MetricOut, status_code=status.HTTP_201_CREATED)
async def create_metric(
    body: MetricCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    normalized = normalize_metric_name(body.name)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Metric name is not valid after normalization.",
        )

    key = to_metric_key(body.name)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not generate a valid metric key from the provided name.",
        )

    existing_key = await metric_repository.get_by_key(db, key)
    if existing_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A metric with key '{key}' already exists.",
        )

    existing_name = await metric_repository.get_by_name(db, body.name.strip())
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A metric named '{body.name}' already exists.",
        )

    all_metrics = await metric_repository.list_all(db)
    similar = find_similar_metric(body.name, all_metrics)
    if similar:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A similar metric already exists: '{similar.name}'. "
                "Please use the existing metric or choose a clearly different name."
            ),
        )

    metric_type = body.metric_type if body.metric_type in ("interaction", "output") else "interaction"
    metric = Metric(
        id=str(uuid4()),
        key=key,
        name=body.name.strip(),
        rubric=body.rubric.strip(),
        metric_type=metric_type,
        is_custom=True,
        created_at=datetime.now(timezone.utc),
    )
    await metric_repository.create(db, metric)
    return _format_metric(metric)


@router.get("/{metric_id}", response_model=MetricOut)
async def get_metric(
    metric_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin", "interviewer")),
):
    metric = await metric_repository.get(db, metric_id)
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric not found",
        )
    return _format_metric(metric)


@router.put("/{metric_id}", response_model=MetricOut)
async def update_metric(
    metric_id: str,
    body: MetricUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    metric = await metric_repository.get(db, metric_id)
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric not found",
        )

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return _format_metric(metric)

    if "name" in updates:
        name = updates["name"].strip()
        normalized = normalize_metric_name(name)
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Metric name is not valid after normalization.",
            )

        existing_name = await metric_repository.get_by_name(db, name)
        if existing_name and existing_name.id != metric_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A metric named '{name}' already exists.",
            )

        all_metrics = [m for m in await metric_repository.list_all(db) if m.id != metric_id]
        similar = find_similar_metric(name, all_metrics)
        if similar:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"A similar metric already exists: '{similar.name}'. "
                    "Please use the existing metric or choose a clearly different name."
                ),
            )

        updates["name"] = name
        updates["key"] = to_metric_key(name)

    if "rubric" in updates:
        updates["rubric"] = updates["rubric"].strip()

    if "metric_type" in updates and updates["metric_type"] is not None:
        updates["metric_type"] = updates["metric_type"].strip()

    metric = await metric_repository.update(db, metric, updates)
    return _format_metric(metric)


@router.delete("/{metric_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_metric(
    metric_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    metric = await metric_repository.get(db, metric_id)
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric not found",
        )

    if not metric.is_custom:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Default metrics cannot be deleted.",
        )

    await metric_repository.delete(db, metric_id)
    return None
