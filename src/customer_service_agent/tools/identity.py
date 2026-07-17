from pydantic import Field, field_validator

from customer_service_agent.schemas import WaybillMixin


class VerifyReceiverInput(WaybillMixin):
    phone_last4: str = Field(pattern=r"^\d{4}$")

    @field_validator("phone_last4")
    @classmethod
    def keep_only_four_digits(cls, value: str) -> str:
        return value.strip()
