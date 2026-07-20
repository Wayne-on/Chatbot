from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class SceneStatus(StrEnum):
    IDLE = "idle"
    COLLECTING = "collecting"
    PROCESSING = "processing"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    TRANSFER = "transfer"
    CANCELLED = "cancelled"


class Intent(StrEnum):
    TRACKING = "tracking"
    PACKAGE_VOLUME = "query_package_volume"
    DELIVERY_FOLLOWUP = "delivery_followup"
    DELIVERED_NOT_RECEIVED = "delivered_not_received"
    CHANGE_ADDRESS = "change_address"
    COMPLAINT = "complaint"
    QUERY_COMPLAINT = "query_complaint"
    FAQ = "faq"
    CONVERSATION = "conversation"
    FALLBACK = "fallback"


class IntentRelation(StrEnum):
    """How secondary goals relate to the primary goal in the same user turn."""

    AFTER = "after"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    ALTERNATIVE = "alternative"
    CORRECTION = "correction"


class ToolStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    user_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    language: str | None = Field(default=None, max_length=16)
    user_credential: SecretStr | None = Field(default=None, repr=False, exclude=True)
    request_id: str | None = Field(default=None, max_length=128)


class ChatResponse(BaseModel):
    reply: str
    status: SceneStatus
    current_intent: Intent | None = None
    current_step: str | None = None
    action_required: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    trace_id: str


class RequestContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    user_id: str
    request_id: str
    trace_id: str
    user_credential: SecretStr | None = Field(default=None, repr=False, exclude=True)


class ToolResult(BaseModel):
    status: ToolStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    message: str = ""
    retryable: bool = False
    trace_id: str

    @property
    def succeeded(self) -> bool:
        return self.status == ToolStatus.SUCCESS


class PendingConfirmation(BaseModel):
    tool: Literal["create_complaint", "urge_delivery", "change_address"]
    arguments: dict[str, Any]
    idempotency_key: str = Field(min_length=16, max_length=128)
    prompt_key: str


class RouteDecision(BaseModel):
    intent: Intent | None = None
    secondary_intents: list[Intent] = Field(default_factory=list, max_length=3)
    intent_relation: IntentRelation = IntentRelation.AFTER
    intent_condition: str | None = Field(default=None, max_length=500)
    language: Literal["en", "vi", "zh"] = "en"
    waybill_no: str | None = None
    invalid_waybill_no: str | None = None
    phone_last4: str | None = None
    ticket_id: str | None = None
    new_address: str | None = None
    cancel_requested: bool = False
    confirmation: bool = False
    rejection: bool = False
    human_requested: bool = False
    modifies_existing: bool = False
    explicit_intent: bool = False
    continuation: bool = False
    semantic_conflict: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    clarify_question: str | None = Field(default=None, max_length=500)
    business_reason: str | None = Field(default=None, max_length=500)
    recommended_tool: (
        Literal[
            "query_tracking",
            "query_package_volume",
            "retrieve_faq",
            "query_complaint",
            "verify_receiver",
            "urge_delivery",
            "create_complaint",
            "check_address_change",
            "change_address",
            "transfer_to_human",
        ]
        | None
    ) = None


class ModelUnderstanding(BaseModel):
    """Constrained semantic plan produced by the LangGraph semantic node."""

    intent: Intent
    secondary_intents: list[Intent] = Field(default_factory=list, max_length=3)
    intent_relation: IntentRelation = IntentRelation.AFTER
    intent_condition: str | None = Field(default=None, max_length=500)
    language: Literal["en", "vi", "zh"]
    waybill_no: str | None = Field(default=None, pattern=r"^(JT\d{8,13}|\d{8,15})$")
    phone_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    ticket_id: str | None = Field(default=None, pattern=r"^(?:MOCK|CMP|TKT|URG)[A-Z0-9-]{6,32}$")
    new_address: str | None = Field(default=None, min_length=8, max_length=500)
    cancel_requested: bool = False
    human_requested: bool = False
    modifies_existing: bool = False
    continuation: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    clarify_question: str | None = Field(default=None, max_length=500)
    business_reason: str | None = Field(default=None, max_length=500)
    recommended_tool: (
        Literal[
            "query_tracking",
            "query_package_volume",
            "retrieve_faq",
            "query_complaint",
            "verify_receiver",
            "urge_delivery",
            "create_complaint",
            "check_address_change",
            "change_address",
            "transfer_to_human",
        ]
        | None
    ) = None

    @field_validator(
        "intent_condition",
        "waybill_no",
        "phone_last4",
        "ticket_id",
        "new_address",
        "clarify_question",
        "business_reason",
        "recommended_tool",
        mode="before",
    )
    @classmethod
    def normalize_model_nulls(cls, value: Any) -> Any:
        """Providers sometimes serialize an absent optional field as the string null."""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.lower() in {"", "null", "none", "n/a"}:
                return None
            return stripped
        return value


class PendingIntent(BaseModel):
    """A recognized user goal waiting behind the single authoritative active scene."""

    intent: Intent
    relation: IntentRelation = IntentRelation.AFTER
    source_message: str = Field(min_length=1, max_length=1000)
    condition: str | None = Field(default=None, max_length=500)
    phone_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    ticket_id: str | None = Field(default=None, max_length=64)
    new_address: str | None = Field(default=None, min_length=8, max_length=500)


class ConversationMessage(BaseModel):
    """One bounded user-visible message retained for short-term conversation context."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class HealthResponse(BaseModel):
    status: Literal["ok", "ready"]


class AuditRecord(BaseModel):
    user_id: str
    waybill_no: str | None = None
    request_id: str
    action: str
    timestamp: str
    idempotency_key: str | None = None
    result_status: str


class WaybillMixin(BaseModel):
    waybill_no: str

    @field_validator("waybill_no")
    @classmethod
    def normalize_waybill(cls, value: str) -> str:
        from customer_service_agent.router import normalize_waybill

        normalized = normalize_waybill(value)
        if normalized is None:
            raise ValueError("invalid waybill number")
        return normalized
