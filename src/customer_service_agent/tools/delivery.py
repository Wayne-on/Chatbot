from pydantic import Field

from customer_service_agent.schemas import WaybillMixin


class UrgeDeliveryInput(WaybillMixin):
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=16, max_length=128)


class CheckAddressChangeInput(WaybillMixin):
    """Read-only eligibility check performed before collecting confirmation."""


class ChangeAddressInput(WaybillMixin):
    new_address: str = Field(min_length=8, max_length=500)
    idempotency_key: str = Field(min_length=16, max_length=128)
