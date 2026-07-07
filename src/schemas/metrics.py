from pydantic import BaseModel, Field


class MetricBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    rubric: str = Field(..., min_length=10)


class MetricCreate(MetricBase):
    pass


class MetricUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    rubric: str | None = Field(None, min_length=10)


class MetricOut(BaseModel):
    id: str
    key: str
    name: str
    rubric: str
    is_custom: bool
    created_at: str


class CustomMetricCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    rubric: str = Field(..., min_length=10)
