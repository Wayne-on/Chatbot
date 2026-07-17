import pytest
from pydantic import ValidationError

from customer_service_agent.adapters.mock_backend import MockBackend
from customer_service_agent.schemas import RequestContext, ToolStatus
from customer_service_agent.tools.complaint import CreateComplaintInput
from customer_service_agent.tools.delivery import ChangeAddressInput
from customer_service_agent.tools.identity import VerifyReceiverInput
from customer_service_agent.tools.service import BusinessTools
from customer_service_agent.tools.tracking import QueryTrackingInput


def context(request_id: str = "request-1") -> RequestContext:
    return RequestContext(
        session_id="session-1",
        user_id="user-1",
        request_id=request_id,
        trace_id="trace-1",
    )


def test_tool_parameter_validation() -> None:
    with pytest.raises(ValidationError):
        QueryTrackingInput(waybill_no="bad")
    with pytest.raises(ValidationError):
        VerifyReceiverInput(waybill_no="JT123456785", phone_last4="12")
    with pytest.raises(ValidationError):
        ChangeAddressInput(
            waybill_no="JT123456781",
            new_address="short",
            idempotency_key="x" * 20,
        )


async def test_tool_result_has_uniform_shape() -> None:
    tools = BusinessTools(MockBackend())
    result = await tools.query_tracking(QueryTrackingInput(waybill_no="JT123456781"), context())
    assert result.status == ToolStatus.SUCCESS
    assert result.error_code is None
    assert result.retryable is False
    assert result.trace_id == "trace-1"
    assert isinstance(result.data, dict)


async def test_tool_timeout_maps_error_without_facts() -> None:
    backend = MockBackend()
    backend.fail_next("query_tracking")
    tools = BusinessTools(backend)
    result = await tools.query_tracking(QueryTrackingInput(waybill_no="JT123456781"), context())
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "BUSINESS_API_TIMEOUT"
    assert result.retryable is True
    assert result.data == {}


async def test_complaint_write_is_idempotent() -> None:
    backend = MockBackend()
    tools = BusinessTools(backend)
    args = CreateComplaintInput(
        waybill_no="JT123456785",
        complaint_type="delivered_not_received",
        description="The parcel was not received by the verified recipient.",
        idempotency_key="same-idempotency-key-001",
    )
    first = await tools.create_complaint(args, context("r1"))
    second = await tools.create_complaint(args, context("r2"))
    assert first.data["ticket_id"] == second.data["ticket_id"]
    assert len([a for a in backend.audit_records if a.action == "create_complaint"]) == 1
