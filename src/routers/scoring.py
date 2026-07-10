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
import json

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
        score_type=score.score_type,
        scores=score.scores,
        overall_score=score.overall_score,
        summary=score.summary,
        red_flags=score.red_flags,
        raw_llm_response=score.raw_llm_response,
        scored_at=score.scored_at.isoformat(),
    )
    

async def _resolve_scoring_metrics_by_type(
    db: AsyncSession, problem: Problem, metric_type: str
) -> list[dict]:
    """Load all metric definitions matching the given metric_type for a given problem.

    Raises HTTPException if no metrics of that type exist.
    """
    metric_ids = problem.metric_ids or []
    metrics: list[Metric] = []
    if metric_ids:
        for metric_id in metric_ids:
            metric = await metric_repository.get(db, metric_id)
            if metric:
                metrics.append(metric)
    if not metrics:
        metrics = await metric_repository.list_defaults(db)
    
    metrics = [m for m in metrics if m.metric_type == metric_type]
            
    if not metrics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No metrics found for metric_type '{metric_type}'.",
        )

    return [
        {"key": metric.key, "name": metric.name, "rubric": metric.rubric}
        for metric in metrics
    ]


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

def _parse_judge_output(interview_id, judge_result, metrics) -> dict:
    parsed = judge_result
    raw_response = json.dumps(judge_result)
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
    
    return score_data


async def _score_interview(interview_id: str) -> tuple | None:
    """Run LLM scoring asynchronously and store the result."""
    async with AsyncSessionLocal() as db:
        interview = await interview_repository.get(db, interview_id)
        if interview is None:
            return None

        if interview.status != "completed":
            return None

        session = await session_repository.get_or_fetch_from_s3(db, interview_id)
        log_key = session.logs_path if session else None

        logs = await session_repository.get_S3_logs(interview_id, log_key)
        if not logs:
            raise NoSessionLogsError()
        logs = [ e for e in logs if (e["type"] == "prompt" or e["type"] == "llm_response")]
        
        files_path = await session_repository.get_files_path(interview_id)
        
        problem = await problem_repository.get(db, interview.problem_id)
        if problem is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Problem not found for this interview",
            )

        await interview_repository.update_scoring_status(db, interview_id, "scoring")

        try:
            problem_statement = problem.markdown_content
            metrics_output = await _resolve_scoring_metrics_by_type(db, problem, "output")
            metrics_interact = await _resolve_scoring_metrics_by_type(db, problem, "interaction")
            judge_result = await asyncio.to_thread(llm_judge.multi_judge_session, logs, problem_statement, files_path, metrics_output, metrics_interact)
        except HTTPException as exc:
            logger.error("LLM scoring failed for interview %s: %s", interview_id, exc.detail)
            await interview_repository.update_scoring_status(db, interview_id, "failed")
            raise
        
        (output_eval_res, turn_eval_res, final_eval_res) = judge_result
        
        output_score = _parse_judge_output(interview_id, output_eval_res, metrics_output)
        output_score["score_type"] = "output"
        interaction_score = _parse_judge_output(interview_id, turn_eval_res, metrics_interact)
        interaction_score["score_type"] = "interaction"

        final_score = {
            "score_type": "final",
            "scores": {},
            "overall_score": 0.0,
            "summary": final_eval_res,
            "red_flags": [],
            "raw_llm_response": final_eval_res,
            "scored_at": datetime.now(timezone.utc),
        }

        async def _upsert_score(score_data: dict) -> Score:
            existing = await score_repository.get_by_interview_and_type(
                db, interview_id, score_data["score_type"]
            )
            if existing is not None:
                return await score_repository.update(db, existing, score_data)
            score_obj = Score(
                id=str(uuid.uuid4()),
                interview_id=interview_id,
                **score_data,
            )
            await score_repository.create(db, score_obj)
            return score_obj

        output_score_obj = await _upsert_score(output_score)
        interaction_score_obj = await _upsert_score(interaction_score)
        final_score_obj = await _upsert_score(final_score)

        await interview_repository.update_scoring_status(db, interview_id, "scored")

        return (
            _format_score(output_score_obj).model_dump(),
            _format_score(interaction_score_obj).model_dump(),
            _format_score(final_score_obj).model_dump(),
        )


async def run_scoring_background(interview_id: str) -> None:
    """Trigger LLM scoring in a background asyncio task."""
    asyncio.create_task(_score_interview(interview_id))


@router.post("/{interview_id}/score", response_model=list[ScoreOut])
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


@router.get("/{interview_id}", response_model=list[ScoreOut])
async def get_score(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin", "interviewer")),
):
    scores = await score_repository.get_by_interview(db, interview_id)
    if not scores:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not scored yet",
        )

    return [_format_score(s).model_dump() for s in scores]


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
            score_type=score.score_type
        )
        results.append(row.model_dump())

    return results
