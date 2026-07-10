from pydantic import BaseModel


class InterviewCreate(BaseModel):
    candidate_id: str
    problem_id: str
    scheduled_at: str


class InterviewUpdate(BaseModel):
    scheduled_at: str | None = None
    status: str | None = None
    problem_id: str | None = None


class InterviewRow(BaseModel):
    id: str
    candidate_id: str
    candidate_email: str
    problem_id: str
    problem_title: str
    scheduled_at: str
    duration_minutes: int
    status: str
    scoring_status: str
    started_at: str | None
    ended_at: str | None
    created_at: str
    overall_score: float | None
    email_sent: bool | None = None
    email_error: str | None = None
