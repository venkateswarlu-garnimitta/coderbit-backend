import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.problem import Problem
from ..repositories.problem_repository import problem_repository
from .seed_metrics import default_metric_ids

logger = logging.getLogger(__name__)

_SEED_FILE = Path(__file__).resolve().parents[2] / "seeds" / "interview_projects.json"
_DEFAULT_DIFFICULTY = "Hard"
_DEFAULT_DURATION_MINUTES = 90


def _parse_duration_minutes(time_estimate: str) -> int:
    text = time_estimate.lower().strip()
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if not numbers:
        return _DEFAULT_DURATION_MINUTES
    if len(numbers) >= 2:
        return max(5, min(180, round((numbers[0] + numbers[1]) / 2 * 60)))
    value = numbers[0]
    if "hour" in text or "hr" in text:
        return max(5, min(180, value * 60))
    if "min" in text:
        return max(5, min(180, value))
    return max(5, min(180, value * 60))


def _format_schema(db_schema: dict[str, Any]) -> str:
    lines: list[str] = []
    for table, columns in db_schema.items():
        lines.append(f"### `{table}`")
        if isinstance(columns, list):
            for column in columns:
                lines.append(f"- `{column}`")
        lines.append("")
    return "\n".join(lines).strip()


def _format_apis(apis: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for api in apis:
        method = api.get("method", "GET")
        path = api.get("path", "/")
        description = api.get("description", "")
        lines.append(f"- **{method}** `{path}` — {description}")
    return "\n".join(lines)


def _format_bullets(items: list[Any]) -> str:
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            aspect = item.get("aspect", "Criteria")
            detail = item.get("detail", "")
            lines.append(f"- **{aspect}:** {detail}")
        else:
            lines.append(f"- {item}")
    return "\n".join(lines)


def project_to_markdown(project: dict[str, Any]) -> str:
    title = project.get("title", "Untitled Project")
    category = project.get("category", "General")
    description = project.get("description", "")

    sections = [
        f"# {title}",
        "",
        f"**Category:** {category}  ",
        "",
        "## Description",
        description,
    ]

    db_schema = project.get("db_schema")
    if isinstance(db_schema, dict) and db_schema:
        sections.extend(["", "## Database Schema", _format_schema(db_schema)])

    apis = project.get("apis")
    if isinstance(apis, list) and apis:
        sections.extend(["", "## Required APIs", _format_apis(apis)])

    constraints = project.get("constraints")
    if isinstance(constraints, list) and constraints:
        sections.extend(["", "## Constraints", _format_bullets(constraints)])

    return "\n".join(sections).strip()


async def _get_by_title(db: AsyncSession, title: str) -> Problem | None:
    result = await db.execute(select(Problem).where(Problem.title == title))
    return result.scalar_one_or_none()


def _load_seed_projects() -> list[dict[str, Any]]:
    if not _SEED_FILE.is_file():
        logger.warning("Problem seed file not found: %s", _SEED_FILE)
        return []

    with _SEED_FILE.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    projects = payload.get("projects")
    if not isinstance(projects, list):
        logger.warning("Problem seed file has no projects array: %s", _SEED_FILE)
        return []

    return [project for project in projects if isinstance(project, dict)]


async def seed_problems(db: AsyncSession) -> tuple[int, int, int]:
    """Insert or refresh seeded problems. Returns (created, updated, skipped)."""
    projects = _load_seed_projects()
    if not projects:
        return 0, 0, 0

    created = 0
    updated = 0
    skipped = 0

    metric_ids = await default_metric_ids(db)

    for project in projects:
        title = str(project.get("title", "")).strip()
        if not title:
            continue

        markdown_content = project_to_markdown(project)
        duration_minutes = _parse_duration_minutes(
            str(project.get("time_estimate", "1-2 hours"))
        )
        difficulty = str(project.get("difficulty") or _DEFAULT_DIFFICULTY)

        existing = await _get_by_title(db, title)
        if existing is not None:
            updates: dict[str, Any] = {}
            if existing.markdown_content != markdown_content:
                updates["markdown_content"] = markdown_content
                updates["duration_minutes"] = duration_minutes
                updates["difficulty"] = difficulty
            if not existing.metric_ids and metric_ids:
                updates["metric_ids"] = metric_ids

            if updates:
                await problem_repository.update(db, existing, updates)
                updated += 1
            else:
                skipped += 1
            continue

        await problem_repository.create_problem(
            db,
            title=title,
            markdown_content=markdown_content,
            duration_minutes=duration_minutes,
            difficulty=difficulty,
            metric_ids=metric_ids,
        )
        created += 1

    if created or updated or skipped:
        logger.info(
            "Problem seed complete: created=%s updated=%s skipped=%s",
            created,
            updated,
            skipped,
        )

    return created, updated, skipped
