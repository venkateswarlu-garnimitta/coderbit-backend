from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db, require_role
from ..lib.metric_validation import find_similar_metric, to_metric_key
from ..models.interview import Interview
from ..models.metric import Metric
from ..models.problem import Problem
from ..models.score import Score
from ..models.session import InterviewSession
from ..repositories.metric_repository import metric_repository
from ..repositories.problem_repository import problem_repository
from ..schemas.metrics import MetricOut
from ..schemas.problems import ProblemCreate, ProblemDetail, ProblemSummary, ProblemUpdate

router = APIRouter(prefix="/problems", tags=["problems"])


def _format_metric(metric: Metric) -> MetricOut:
    return MetricOut(
        id=metric.id,
        key=metric.key,
        name=metric.name,
        rubric=metric.rubric,
        is_custom=metric.is_custom,
        created_at=metric.created_at.isoformat(),
    )


async def _resolve_metrics(
    db: AsyncSession, metric_ids: list[str]
) -> list[Metric]:
    metrics: list[Metric] = []
    for mid in metric_ids:
        metric = await metric_repository.get(db, mid)
        if metric:
            metrics.append(metric)
    return metrics


async def _create_custom_metrics(
    db: AsyncSession,
    custom_metrics: list,
) -> list[Metric]:
    """Persist custom metrics after validating against existing metrics."""
    created: list[Metric] = []
    if not custom_metrics:
        return created

    all_metrics = await metric_repository.list_all(db)

    for cm in custom_metrics:
        name = cm.name.strip()
        rubric = cm.rubric.strip()
        key = to_metric_key(name)

        if not key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not generate a valid key for metric '{name}'.",
            )

        existing_by_key = await metric_repository.get_by_key(db, key)
        if existing_by_key:
            created.append(existing_by_key)
            continue

        similar = find_similar_metric(name, all_metrics)
        if similar:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Custom metric '{name}' is too similar to existing metric "
                    f"'{similar.name}'. Please use the existing metric or choose a clearly different name."
                ),
            )

        metric = Metric(
            id=str(uuid4()),
            key=key,
            name=name,
            rubric=rubric,
            is_custom=True,
            created_at=datetime.now(timezone.utc),
        )
        await metric_repository.create(db, metric)
        all_metrics.append(metric)
        created.append(metric)

    return created


def _format_problem(problem: Problem, metrics: list[Metric] | None = None) -> ProblemDetail:
    return ProblemDetail(
        id=problem.id,
        title=problem.title,
        difficulty=problem.difficulty,
        markdown_content=problem.markdown_content,
        duration_minutes=problem.duration_minutes,
        metric_ids=problem.metric_ids or [],
        metrics=[_format_metric(m) for m in metrics or []],
        created_at=problem.created_at.isoformat(),
    )


def _summarize_problem(problem: Problem) -> ProblemSummary:
    return ProblemSummary(
        id=problem.id,
        title=problem.title,
        difficulty=problem.difficulty,
        duration_minutes=problem.duration_minutes,
        markdown_content=problem.markdown_content,
        metric_ids=problem.metric_ids or [],
        created_at=problem.created_at.isoformat(),
    )


@router.post("", response_model=ProblemDetail, status_code=status.HTTP_201_CREATED)
async def create_problem(
    body: ProblemCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
) -> ProblemDetail:
    custom_metrics = await _create_custom_metrics(db, body.custom_metrics)
    metric_ids = list(body.metric_ids)
    for metric in custom_metrics:
        if metric.id not in metric_ids:
            metric_ids.append(metric.id)

    problem = await problem_repository.create_problem(
        db,
        title=body.title,
        markdown_content=body.markdown_content,
        duration_minutes=body.duration_minutes,
        difficulty=body.difficulty,
        metric_ids=metric_ids,
    )

    metrics = await _resolve_metrics(db, metric_ids)
    return _format_problem(problem, metrics)


@router.get("", response_model=list[ProblemSummary])
async def list_problems(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin", "interviewer")),
) -> list[ProblemSummary]:
    problems = await problem_repository.get_all(db)
    return [_summarize_problem(p) for p in problems]


@router.get("/{problem_id}", response_model=ProblemDetail)
async def get_problem(
    problem_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
) -> ProblemDetail:
    problem = await problem_repository.get(db, problem_id)
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found"
        )
    metrics = await _resolve_metrics(db, problem.metric_ids or [])
    return _format_problem(problem, metrics)


@router.put("/{problem_id}", response_model=ProblemDetail)
async def update_problem(
    problem_id: str,
    body: ProblemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
) -> ProblemDetail:
    problem = await problem_repository.get(db, problem_id)
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found"
        )

    updates = body.model_dump(exclude_unset=True)

    if "custom_metrics" in updates:
        custom_metrics = await _create_custom_metrics(db, body.custom_metrics)
        updates.pop("custom_metrics")
        current_metric_ids = set(updates.get("metric_ids", problem.metric_ids or []))
        for metric in custom_metrics:
            current_metric_ids.add(metric.id)
        updates["metric_ids"] = list(current_metric_ids)

    if updates:
        problem = await problem_repository.update(db, problem, updates)

    metrics = await _resolve_metrics(db, problem.metric_ids or [])
    return _format_problem(problem, metrics)


@router.delete("/{problem_id}")
async def delete_problem(
    problem_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
) -> dict:
    problem = await problem_repository.get(db, problem_id)
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found"
        )

    # Delete all interviews (and their scores/sessions) referencing this problem
    stmt = select(Interview).where(Interview.problem_id == problem_id)
    result = await db.execute(stmt)
    interviews = list(result.scalars().all())
    for interview in interviews:
        score_stmt = select(Score).where(Score.interview_id == interview.id)
        score_result = await db.execute(score_stmt)
        for s in score_result.scalars().all():
            await db.delete(s)

        session_stmt = select(InterviewSession).where(
            InterviewSession.interview_id == interview.id
        )
        session_result = await db.execute(session_stmt)
        for s in session_result.scalars().all():
            await db.delete(s)

        await db.delete(interview)
    await db.commit()

    await problem_repository.delete(db, problem_id)
    return {"message": "deleted"}
