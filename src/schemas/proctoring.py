from pydantic import BaseModel, Field


class ProctoringAlertCreate(BaseModel):
    alert_type: str = Field(
        ...,
        description=(
            "multiple_faces | head_movement | looking_away | no_face | phone_detected"
        ),
    )
    captured_at: str
    session_elapsed_ms: int = Field(..., ge=0)


class ProctoringAlertRow(BaseModel):
    id: str
    alert_type: str
    captured_at: str
    session_elapsed_ms: int
    image_url: str | None = None
