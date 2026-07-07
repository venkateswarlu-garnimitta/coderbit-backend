import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import AsyncSessionLocal
from ..lib import llm_judge
from ..lib.metric_validation import find_similar_metric, to_metric_key
from ..models.metric import Metric
from ..models.problem import Problem
from ..models.score import Score
from ..repositories.interview_repository import interview_repository
from ..repositories.metric_repository import metric_repository
from ..repositories.problem_repository import problem_repository
from ..repositories.score_repository import score_repository
from ..repositories.session_repository import session_repository
from ..schemas.scoring import ScoreListRow, ScoreOut
from ..dependencies import get_db, require_role

router = APIRouter(prefix="/scoring", tags=["scoring"])

logger = logging.getLogger(__name__)


class NoSessionLogsError(Exception):
    """Raised when scoring is requested but no session logs exist."""


def _compute_overall_score(scores: dict[str, float]) -> float:
    if not scores:
        return 0.0
    return round(sum(scores.values()) / len(scores), 2)


def _format_score(score: Score) -> ScoreOut:
    return ScoreOut(
        id=score.id,
        interview_id=score.interview_id,
        scores=score.scores,
        overall_score=score.overall_score,
        summary=score.summary,
        red_flags=score.red_flags,
        raw_llm_response=score.raw_llm_response,
        scored_at=score.scored_at.isoformat(),
    )


async def _resolve_scoring_metrics(
    db: AsyncSession, problem: Problem
) -> list[dict]:
    """Load metric definitions for a problem, falling back to defaults if none selected."""
    metric_ids = problem.metric_ids or []
    metrics: list[Metric] = []

    if metric_ids:
        for metric_id in metric_ids:
            metric = await metric_repository.get(db, metric_id)
            if metric:
                metrics.append(metric)

    if not metrics:
        metrics = await metric_repository.list_defaults(db)

    if not metrics:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No scoring metrics are configured for this problem.",
        )

    return [
        {"key": metric.key, "name": metric.name, "rubric": metric.rubric}
        for metric in metrics
    ]


async def _score_interview(interview_id: str) -> dict | None:
    """Run LLM scoring asynchronously and store the result."""
    async with AsyncSessionLocal() as db:
        interview = await interview_repository.get(db, interview_id)
        if interview is None:
            return None

        if interview.status != "completed":
            return None

        session = await session_repository.get_by_interview(db, interview_id)
        if session is None:
            raise NoSessionLogsError()

        logs = await session_repository.get_logs(db, session.id)
        if not logs:
            raise NoSessionLogsError()

        problem = await problem_repository.get(db, interview.problem_id)
        if problem is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Problem not found for this interview",
            )

        await interview_repository.update_scoring_status(db, interview_id, "scoring")

        try:
            metrics = await _resolve_scoring_metrics(db, problem)
            judge_result = await asyncio.to_thread(llm_judge.judge_session, logs, metrics)
        except HTTPException as exc:
            logger.error("LLM scoring failed for interview %s: %s", interview_id, exc.detail)
            await interview_repository.update_scoring_status(db, interview_id, "failed")
            raise

        parsed = judge_result["parsed"]
        raw_response = judge_result["raw"]
        scored_at = datetime.now(timezone.utc)

        llm_scores = parsed.get("scores", {})
        expected_keys = {m["key"] for m in metrics}
        missing_keys = expected_keys - set(llm_scores.keys())
        if missing_keys:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM response missing scores for: {', '.join(sorted(missing_keys))}",
            )

        scores = {key: float(llm_scores[key]) for key in expected_keys}
        overall_score = _compute_overall_score(scores)

        score_data = {
            "scores": scores,
            "overall_score": overall_score,
            "summary": parsed.get("summary", ""),
            "red_flags": parsed.get("red_flags", []),
            "raw_llm_response": raw_response,
            "scored_at": scored_at,
        }

        existing = await score_repository.get_by_interview(db, interview_id)
        if existing is not None:
            score = await score_repository.update(db, existing, score_data)
        else:
            score = Score(
                id=str(uuid.uuid4()),
                interview_id=interview_id,
                **score_data,
            )
            await score_repository.create(db, score)
        await interview_repository.update_scoring_status(db, interview_id, "scored")

        return _format_score(score).model_dump()


async def run_scoring_background(interview_id: str) -> None:
    """Trigger LLM scoring in a background asyncio task."""
    asyncio.create_task(_score_interview(interview_id))


@router.post("/{interview_id}/score", response_model=ScoreOut)
async def score_interview(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin", "interviewer")),
):
    interview = await interview_repository.get(db, interview_id)
    if interview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Interview not found",
        )

    if interview.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interview must be completed before scoring",
        )

    try:
        result = await _score_interview(interview_id)
    except NoSessionLogsError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No session log found. The candidate session must complete before scoring.",
        ) from None
    except HTTPException as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No session log found. The candidate session must complete before scoring.",
        )
    return result


@router.get("/{interview_id}", response_model=ScoreOut)
async def get_score(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin", "interviewer")),
):
    score = await score_repository.get_by_interview(db, interview_id)
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not scored yet",
        )

    return _format_score(score).model_dump()


@router.get("/", response_model=list[ScoreListRow])
async def list_scores(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    scores = await score_repository.list_with_details(db)

    results = []
    for score in scores:
        row = ScoreListRow(
            id=score.id,
            interview_id=score.interview_id,
            candidate_email=score.interview.candidate.email,
            problem_title=score.interview.problem.title,
            scores=score.scores,
            overall_score=score.overall_score,
            summary=score.summary,
            red_flags=score.red_flags,
            raw_llm_response=score.raw_llm_response,
            scored_at=score.scored_at.isoformat(),
        )
        results.append(row.model_dump())

    return results
