from pydantic import BaseModel, Field

from .metrics import CustomMetricCreate, MetricOut


class ProblemCreate(BaseModel):
    title: str = Field(..., min_length=1)
    markdown_content: str = Field(..., min_length=1)
    duration_minutes: int = Field(..., ge=5, le=180)
    difficulty: str = "Medium"
    metric_ids: list[str] = Field(default_factory=list)
    custom_metrics: list[CustomMetricCreate] = Field(default_factory=list)


class ProblemUpdate(BaseModel):
    title: str | None = Field(None, min_length=1)
    markdown_content: str | None = Field(None, min_length=1)
    duration_minutes: int | None = Field(None, ge=5, le=180)
    difficulty: str | None = None
    metric_ids: list[str] | None = None
    custom_metrics: list[CustomMetricCreate] | None = None


class ProblemSummary(BaseModel):
    id: str
    title: str
    difficulty: str = "Medium"
    duration_minutes: int
    markdown_content: str
    metric_ids: list[str]
    created_at: str


class ProblemDetail(BaseModel):
    id: str
    title: str
    difficulty: str = "Medium"
    markdown_content: str
    duration_minutes: int
    metric_ids: list[str]
    metrics: list[MetricOut]
    created_at: str
