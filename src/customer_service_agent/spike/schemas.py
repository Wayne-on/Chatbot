from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpikeScenario(StrEnum):
    MULTI_PARCEL_RESOLUTION = "multi_parcel_resolution"
    CROSSBORDER_CUSTOMS = "crossborder_customs"


class SpikeRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


class SpikePlanItem(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    status: Literal["pending", "in_progress", "completed", "failed", "skipped"]


class SpikeEvent(BaseModel):
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=64)
    source: Literal["runtime", "agent", "skill", "subagent", "tool", "human"]
    title: str = Field(min_length=1, max_length=200)
    status: Literal["pending", "running", "completed", "failed", "paused", "cancelled"]
    occurred_at: datetime
    safe_data: dict[str, Any] = Field(default_factory=dict)


class SpikePendingAction(BaseModel):
    kind: Literal["input", "approval"]
    actions: list[dict[str, Any]] = Field(min_length=1)
    allowed_decisions: list[str] = Field(default_factory=list)
    prompt: str = Field(min_length=1, max_length=1500)


class SpikeRunCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    user_id: str = Field(min_length=1, max_length=128)
    scenario: SpikeScenario
    message: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="zh-CN", max_length=16)


class SpikeRunAccepted(BaseModel):
    run_id: str
    access_token: str
    status: SpikeRunStatus
    trace_id: str


class SpikeRunSnapshot(BaseModel):
    run_id: str
    scenario: SpikeScenario
    scenario_title: str
    objective: str
    status: SpikeRunStatus
    reply: str | None = None
    plan: list[SpikePlanItem] = Field(default_factory=list)
    events: list[SpikeEvent] = Field(default_factory=list)
    pending_action: SpikePendingAction | None = None
    checkpoint_version: int = Field(default=0, ge=0)
    result: dict[str, Any] = Field(default_factory=dict)
    trace_id: str
    created_at: datetime
    updated_at: datetime


class SpikeRunResumeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    access_token: str = Field(min_length=16, max_length=256)
    checkpoint_version: int = Field(ge=1)
    decision: Literal["respond", "approve", "reject"]
    message: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_message_for_response(self) -> SpikeRunResumeRequest:
        if self.decision == "respond" and not self.message:
            raise ValueError("message is required when decision=respond")
        return self


class SpikeRunAccess(BaseModel):
    access_token: str = Field(min_length=16, max_length=256)
