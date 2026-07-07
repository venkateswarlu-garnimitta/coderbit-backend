from pydantic import BaseModel, Field


class ScoreOut(BaseModel):
    id: str
    interview_id: str
    scores: dict[str, float]
    overall_score: float
    summary: str
    red_flags: list[str]
    raw_llm_response: str
    scored_at: str


class ScoreListRow(ScoreOut):
    candidate_email: str
    problem_title: str
