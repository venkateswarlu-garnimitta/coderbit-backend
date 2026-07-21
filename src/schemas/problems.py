from pydantic import BaseModel, Field

from .metrics import CustomMetricCreate, MetricOut

# Allowed service identifiers. Validated in the router so the DB never stores
# arbitrary strings that entrypoint.sh would try to start.
ALLOWED_SERVICES = frozenset({"postgres", "redis", "mongodb"})


class ProblemCreate(BaseModel):
    title: str = Field(..., min_length=1)
    markdown_content: str = Field(..., min_length=1)
    duration_minutes: int = Field(..., ge=5, le=180)
    difficulty: str = "Medium"
    acceptance_criteria: str | None = None
    metric_ids: list[str] = Field(default_factory=list)
    custom_metrics: list[CustomMetricCreate] = Field(default_factory=list)
    required_services: list[str] = Field(default_factory=list)
    allow_assistant: bool = True


class ProblemUpdate(BaseModel):
    title: str | None = Field(None, min_length=1)
    markdown_content: str | None = Field(None, min_length=1)
    duration_minutes: int | None = Field(None, ge=5, le=180)
    difficulty: str | None = None
    acceptance_criteria: str | None = None
    metric_ids: list[str] | None = None
    custom_metrics: list[CustomMetricCreate] | None = None
    required_services: list[str] | None = None
    allow_assistant: bool | None = None


class ProblemSummary(BaseModel):
    id: str
    title: str
    difficulty: str = "Medium"
    duration_minutes: int
    markdown_content: str
    acceptance_criteria: str | None = None
    metric_ids: list[str]
    required_services: list[str] = Field(default_factory=list)
    allow_assistant: bool = True
    created_at: str


class ProblemDetail(BaseModel):
    id: str
    title: str
    difficulty: str = "Medium"
    markdown_content: str
    duration_minutes: int
    acceptance_criteria: str | None = None
    metric_ids: list[str]
    metrics: list[MetricOut]
    required_services: list[str] = Field(default_factory=list)
    allow_assistant: bool = True
    created_at: str
