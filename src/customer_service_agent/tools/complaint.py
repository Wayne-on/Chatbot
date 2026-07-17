from pydantic import BaseModel, Field, field_validator

from customer_service_agent.schemas import WaybillMixin


class CreateComplaintInput(WaybillMixin):
    complaint_type: str = Field(min_length=3, max_length=64)
    description: str = Field(min_length=5, max_length=1000)
    idempotency_key: str = Field(min_length=16, max_length=128)


class QueryComplaintInput(BaseModel):
    ticket_id: str = Field(min_length=8, max_length=40)

    @field_validator("ticket_id")
    @classmethod
    def normalize_ticket(cls, value: str) -> str:
        return value.strip().upper()
