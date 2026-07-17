from pydantic import Field

from customer_service_agent.schemas import WaybillMixin


class QueryTrackingInput(WaybillMixin):
    """Query current tracking facts; never use it to mutate a shipment."""


class QueryPackageVolumeInput(WaybillMixin):
    """Query measured package dimensions and volume for one shipment."""


class TrackingEventLimit(WaybillMixin):
    include_details: bool = True
    event_limit: int = Field(default=10, ge=1, le=50)
