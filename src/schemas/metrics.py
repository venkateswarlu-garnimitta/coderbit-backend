from pydantic import BaseModel, Field


class MetricBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    rubric: str = Field(..., min_length=10)
    metric_type: str | None = None


class MetricCreate(MetricBase):
    pass


class MetricUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    rubric: str | None = Field(None, min_length=10)
    metric_type: str | None = None


class MetricOut(BaseModel):
    id: str
    key: str
    name: str
    rubric: str
    metric_type: str
    is_custom: bool
    metric_type: str | None
    created_at: str


class CustomMetricCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    rubric: str = Field(..., min_length=10)
    metric_type: str | None = None
