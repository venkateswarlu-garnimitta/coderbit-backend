from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    role: Literal["admin", "interviewer", "candidate"]


class UpdateUserRequest(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=6)
    role: Optional[Literal["admin", "interviewer", "candidate"]] = None


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    created_at: str
