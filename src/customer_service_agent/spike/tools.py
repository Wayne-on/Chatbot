from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from langchain.tools import ToolRuntime, tool

from customer_service_agent.schemas import RequestContext, ToolResult
from customer_service_agent.spike.backend import MockSpikeBackend
from customer_service_agent.tools.complaint import CreateComplaintInput
from customer_service_agent.tools.delivery import (
    ChangeAddressInput,
    CheckAddressChangeInput,
    UrgeDeliveryInput,
)
from customer_service_agent.tools.identity import VerifyReceiverInput
from customer_service_agent.tools.service import BusinessTools
from customer_service_agent.tools.tracking import QueryTrackingInput

EventEmitter = Callable[..., Awaitable[None]]


@dataclass
class SpikeAgentContext:
    run_id: str
    scenario: str
    user_id: str
    trace_id: str
    business_tools: BusinessTools
    spike_backend: MockSpikeBackend
    allowed_waybills: set[str]
    allowed_case_ids: set[str]
    facts: dict[str, Any] = field(default_factory=dict)
    emit: EventEmitter | None = None

    @property
    def request_context(self) -> RequestContext:
        return RequestContext(
            session_id=f"spike:{self.run_id}",
            user_id=self.user_id,
            request_id=self.run_id,
            trace_id=self.trace_id,
        )


async def _recorded_call(
    runtime: ToolRuntime[SpikeAgentContext],
    tool_name: str,
    safe_input: dict[str, Any],
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    context = runtime.context
    evidence_id = f"EVD-{uuid4().hex[:10].upper()}"
    if context.emit:
        await context.emit(
            event_type="tool_started",
            source="tool",
            title=tool_name,
            status="running",
            safe_data={"evidence_id": evidence_id, "input": _safe(safe_input)},
        )
    try:
        result = await operation()
    except Exception as exc:
        if context.emit:
            await context.emit(
                event_type="tool_failed",
                source="tool",
                title=tool_name,
                status="failed",
                safe_data={"evidence_id": evidence_id, "error": type(exc).__name__},
            )
        raise
    enriched = dict(result)
    enriched["evidence_id"] = evidence_id
    if context.emit:
        await context.emit(
            event_type="tool_completed",
            source="tool",
            title=tool_name,
            status="completed",
            safe_data={"evidence_id": evidence_id, "output": _safe(enriched)},
        )
    return enriched


def _tool_data(result: ToolResult) -> dict[str, Any]:
    if not result.succeeded:
        raise RuntimeError(result.error_code or "business tool failed")
    return result.data


def _require_waybill(context: SpikeAgentContext, waybill_no: str) -> str:
    normalized = waybill_no.strip().upper()
    if normalized not in context.allowed_waybills:
        raise ValueError("waybill is outside this task's allowlist")
    return normalized


def _require_case(context: SpikeAgentContext, case_id: str) -> str:
    normalized = case_id.strip().upper()
    if normalized not in context.allowed_case_ids:
        raise ValueError("case is outside this task's allowlist")
    return normalized


def _idempotency(context: SpikeAgentContext, action: str, subject: str) -> str:
    raw = f"{context.run_id}|{action}|{subject}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, child in value.items():
            if key in {"phone_last4", "idempotency_key"}:
                safe[key] = "****"
            elif key in {"new_address", "address"} and isinstance(child, str):
                safe[key] = child[:12] + "…" if len(child) > 12 else child
            else:
                safe[key] = _safe(child)
        return safe
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


@tool
async def resolve_order_waybills(
    order_id: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Resolve all shipment waybills belonging to an order before investigating them."""
    return await _recorded_call(
        runtime,
        "resolve_order_waybills",
        {"order_id": order_id},
        lambda: runtime.context.spike_backend.resolve_order_waybills(order_id),
    )


@tool
async def query_tracking(
    waybill_no: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Read the latest authoritative tracking profile for an allowed waybill."""
    waybill = _require_waybill(runtime.context, waybill_no)
    return await _recorded_call(
        runtime,
        "query_tracking",
        {"waybill_no": waybill},
        lambda: _business_result(
            runtime.context.business_tools.query_tracking(
                QueryTrackingInput(waybill_no=waybill), runtime.context.request_context
            )
        ),
    )


@tool
async def query_existing_cases(
    waybill_no: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Check existing investigation, complaint, or follow-up cases to avoid duplicates."""
    waybill = _require_waybill(runtime.context, waybill_no)
    return await _recorded_call(
        runtime,
        "query_existing_cases",
        {"waybill_no": waybill},
        lambda: runtime.context.spike_backend.query_existing_cases(waybill),
    )


@tool
async def query_pod_evidence(
    waybill_no: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Read proof-of-delivery evidence and mismatch flags for a delivered shipment."""
    waybill = _require_waybill(runtime.context, waybill_no)
    return await _recorded_call(
        runtime,
        "query_pod_evidence",
        {"waybill_no": waybill},
        lambda: runtime.context.spike_backend.query_pod_evidence(waybill),
    )


@tool
async def check_address_change(
    waybill_no: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Check whether an allowed shipment is still eligible for an address change."""
    waybill = _require_waybill(runtime.context, waybill_no)
    return await _recorded_call(
        runtime,
        "check_address_change",
        {"waybill_no": waybill},
        lambda: _business_result(
            runtime.context.business_tools.check_address_change(
                CheckAddressChangeInput(waybill_no=waybill), runtime.context.request_context
            )
        ),
    )


@tool
async def check_outlet_hold(
    waybill_no: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Check whether an out-for-delivery shipment can be held at its delivery outlet."""
    waybill = _require_waybill(runtime.context, waybill_no)
    return await _recorded_call(
        runtime,
        "check_outlet_hold",
        {"waybill_no": waybill},
        lambda: runtime.context.spike_backend.check_outlet_hold(waybill),
    )


@tool
async def verify_receiver(
    waybill_no: str,
    phone_last4: str,
    runtime: ToolRuntime[SpikeAgentContext],
) -> dict[str, Any]:
    """Verify the receiver before preparing a delivered-not-received investigation."""
    waybill = _require_waybill(runtime.context, waybill_no)
    protected_last4 = runtime.context.facts.get("user_supplied_phone_last4")
    if not protected_last4 or phone_last4 != protected_last4:
        raise ValueError("phone last four digits must come from the user's external response")

    async def operation() -> dict[str, Any]:
        result = await runtime.context.business_tools.verify_receiver(
            VerifyReceiverInput(waybill_no=waybill, phone_last4=phone_last4),
            runtime.context.request_context,
        )
        data = _tool_data(result)
        if data.get("verified"):
            runtime.context.facts.setdefault("verified_waybills", set()).add(waybill)
        return data

    return await _recorded_call(
        runtime,
        "verify_receiver",
        {"waybill_no": waybill, "phone_last4": phone_last4},
        operation,
    )


@tool
async def request_information(
    question: str,
    requested_fields: list[str],
    reason: str,
) -> str:
    """Pause the task and request missing information from the user in one consolidated question."""
    return f"Information received for {requested_fields}: {question} ({reason})"


@tool
async def submit_address_change(
    waybill_no: str,
    new_address: str,
    reason: str,
    runtime: ToolRuntime[SpikeAgentContext],
) -> dict[str, Any]:
    """Submit an eligible address change. This is a write action requiring approval."""
    waybill = _require_waybill(runtime.context, waybill_no)
    protected_address = runtime.context.facts.get("user_supplied_new_address")
    if not protected_address or new_address.strip() != str(protected_address).strip():
        raise ValueError("new address must exactly match the user's external response")

    async def operation() -> dict[str, Any]:
        eligibility = await runtime.context.business_tools.check_address_change(
            CheckAddressChangeInput(waybill_no=waybill), runtime.context.request_context
        )
        eligibility_data = _tool_data(eligibility)
        if not eligibility_data.get("can_change"):
            raise ValueError("shipment is no longer eligible for address change")
        result = await runtime.context.business_tools.change_address(
            ChangeAddressInput(
                waybill_no=waybill,
                new_address=new_address,
                idempotency_key=_idempotency(runtime.context, "change_address", waybill),
            ),
            runtime.context.request_context,
        )
        data = _tool_data(result) | {"reason": reason}
        if data.get("accepted"):
            runtime.context.facts["address_change_request_id"] = data.get("request_id")
        return data

    return await _recorded_call(
        runtime,
        "submit_address_change",
        {"waybill_no": waybill, "new_address": new_address, "reason": reason},
        operation,
    )


@tool
async def submit_delivery_followup(
    waybill_no: str,
    reason: str,
    runtime: ToolRuntime[SpikeAgentContext],
) -> dict[str, Any]:
    """Create a delivery follow-up for a delayed shipment. Requires approval."""
    waybill = _require_waybill(runtime.context, waybill_no)

    async def operation() -> dict[str, Any]:
        existing = await runtime.context.spike_backend.query_existing_cases(waybill)
        terminal_statuses = {"closed", "closed_without_movement", "resolved", "cancelled"}
        if any(
            str(item.get("status", "")).lower() not in terminal_statuses
            for item in existing.get("cases", [])
        ):
            raise ValueError("an equivalent active follow-up case already exists")
        tracking = await runtime.context.business_tools.query_tracking(
            QueryTrackingInput(waybill_no=waybill), runtime.context.request_context
        )
        if not _tool_data(tracking).get("can_urge"):
            raise ValueError("shipment is no longer eligible for follow-up")
        result = await runtime.context.business_tools.urge_delivery(
            UrgeDeliveryInput(
                waybill_no=waybill,
                reason=reason,
                idempotency_key=_idempotency(runtime.context, "urge_delivery", waybill),
            ),
            runtime.context.request_context,
        )
        data = _tool_data(result)
        if data.get("accepted"):
            runtime.context.facts["delivery_followup_ticket_id"] = data.get("ticket_id")
        return data

    return await _recorded_call(
        runtime,
        "submit_delivery_followup",
        {"waybill_no": waybill, "reason": reason},
        operation,
    )


@tool
async def submit_missing_delivery_case(
    waybill_no: str,
    description: str,
    runtime: ToolRuntime[SpikeAgentContext],
) -> dict[str, Any]:
    """Create a delivered-not-received case after receiver verification. Requires approval."""
    waybill = _require_waybill(runtime.context, waybill_no)

    async def operation() -> dict[str, Any]:
        verified = runtime.context.facts.get("verified_waybills", set())
        if waybill not in verified:
            raise ValueError("receiver verification is required")
        tracking = await runtime.context.business_tools.query_tracking(
            QueryTrackingInput(waybill_no=waybill), runtime.context.request_context
        )
        if _tool_data(tracking).get("status") != "delivered":
            raise ValueError("shipment is no longer marked delivered")
        result = await runtime.context.business_tools.create_complaint(
            CreateComplaintInput(
                waybill_no=waybill,
                complaint_type="delivered_not_received",
                description=description,
                idempotency_key=_idempotency(runtime.context, "missing_delivery", waybill),
            ),
            runtime.context.request_context,
        )
        data = _tool_data(result)
        if data.get("ticket_id"):
            runtime.context.facts["missing_delivery_ticket_id"] = data.get("ticket_id")
        return data

    return await _recorded_call(
        runtime,
        "submit_missing_delivery_case",
        {"waybill_no": waybill, "description": description},
        operation,
    )


@tool
async def submit_outlet_hold(
    waybill_no: str,
    reason: str,
    runtime: ToolRuntime[SpikeAgentContext],
) -> dict[str, Any]:
    """Request a temporary outlet hold for an eligible shipment. Requires approval."""
    waybill = _require_waybill(runtime.context, waybill_no)

    async def operation() -> dict[str, Any]:
        result = await runtime.context.spike_backend.request_outlet_hold(
            waybill,
            reason,
            _idempotency(runtime.context, "outlet_hold", waybill),
        )
        if result.get("accepted"):
            runtime.context.facts["outlet_hold_request_id"] = result.get("hold_request_id")
        return result

    return await _recorded_call(
        runtime,
        "submit_outlet_hold",
        {"waybill_no": waybill, "reason": reason},
        operation,
    )


@tool
async def query_crossborder_case(
    case_id: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Read the current cross-border customs exception state."""
    case = _require_case(runtime.context, case_id)
    return await _recorded_call(
        runtime,
        "query_crossborder_case",
        {"case_id": case},
        lambda: runtime.context.spike_backend.query_crossborder_case(case),
    )


@tool
async def get_customs_declaration(
    case_id: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Read declared items and documents already attached to the customs case."""
    case = _require_case(runtime.context, case_id)
    return await _recorded_call(
        runtime,
        "get_customs_declaration",
        {"case_id": case},
        lambda: runtime.context.spike_backend.get_customs_declaration(case),
    )


@tool
async def retrieve_route_policy(
    route: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Retrieve the versioned mock route policy for declared item categories."""
    return await _recorded_call(
        runtime,
        "retrieve_route_policy",
        {"route": route},
        lambda: runtime.context.spike_backend.retrieve_route_policy(route),
    )


@tool
async def inspect_document_bundle(
    case_id: str,
    document_ids: list[str],
    runtime: ToolRuntime[SpikeAgentContext],
) -> dict[str, Any]:
    """Inspect supplied document IDs and report validity and missing document types."""
    case = _require_case(runtime.context, case_id)

    async def operation() -> dict[str, Any]:
        supplied = set(runtime.context.facts.get("user_supplied_document_ids", []))
        on_file = {"DOC-INVOICE-001"}
        if not set(document_ids).issubset(supplied | on_file):
            raise ValueError("document IDs must come from the user response or existing case")
        result = await runtime.context.spike_backend.inspect_document_bundle(case, document_ids)
        runtime.context.facts["customs_document_ids"] = list(document_ids)
        runtime.context.facts["customs_bundle_complete"] = bool(result["complete"])
        return result

    return await _recorded_call(
        runtime,
        "inspect_document_bundle",
        {"case_id": case, "document_ids": document_ids},
        operation,
    )


@tool
async def quote_customs_options(
    case_id: str, runtime: ToolRuntime[SpikeAgentContext]
) -> dict[str, Any]:
    """Get current costs for supplementing documents or returning the shipment."""
    case = _require_case(runtime.context, case_id)

    async def operation() -> dict[str, Any]:
        result = await runtime.context.spike_backend.quote_customs_options(case)
        runtime.context.facts["customs_quote"] = result
        return result

    return await _recorded_call(
        runtime,
        "quote_customs_options",
        {"case_id": case},
        operation,
    )


@tool
async def submit_compliance_review(
    case_id: str,
    document_ids: list[str],
    runtime: ToolRuntime[SpikeAgentContext],
) -> dict[str, Any]:
    """Prepare the validated document pack for a simulated human compliance review."""
    case = _require_case(runtime.context, case_id)

    async def operation() -> dict[str, Any]:
        supplied = set(runtime.context.facts.get("user_supplied_document_ids", []))
        if not set(document_ids).issubset(supplied | {"DOC-INVOICE-001"}):
            raise ValueError("review documents must come from the user response or existing case")
        inspected_documents = sorted(runtime.context.facts.get("customs_document_ids", []))
        if sorted(document_ids) != inspected_documents:
            raise ValueError("compliance review must use the currently inspected document bundle")
        result = await runtime.context.spike_backend.submit_compliance_review(case, document_ids)
        runtime.context.facts["compliance_review_id"] = result["review_id"]
        runtime.context.facts["reviewed_document_ids"] = sorted(document_ids)
        runtime.context.facts["reviewed_bundle_complete"] = bool(result["bundle_complete"])
        return result

    return await _recorded_call(
        runtime,
        "submit_compliance_review",
        {"case_id": case, "document_ids": document_ids},
        operation,
    )


@tool
async def request_compliance_decision(review_id: str, evidence_summary: str) -> str:
    """Pause and request a simulated human compliance approve/reject decision."""
    return f"Compliance decision received for {review_id}: {evidence_summary}"


@tool
async def submit_customs_documents(
    case_id: str,
    document_ids: list[str],
    runtime: ToolRuntime[SpikeAgentContext],
) -> dict[str, Any]:
    """Submit an approved complete document pack to customs. Requires user approval."""
    case = _require_case(runtime.context, case_id)

    async def operation() -> dict[str, Any]:
        if runtime.context.facts.get("compliance_approved") is not True:
            raise ValueError("human compliance approval is required")
        requested_documents = sorted(document_ids)
        reviewed_documents = sorted(runtime.context.facts.get("reviewed_document_ids", []))
        decided_documents = sorted(runtime.context.facts.get("decided_document_ids", []))
        supplied_documents = set(runtime.context.facts.get("user_supplied_document_ids", []))
        if not set(requested_documents).issubset(supplied_documents | {"DOC-INVOICE-001"}):
            raise ValueError(
                "submitted documents must come from the user response or existing case"
            )
        if requested_documents != reviewed_documents or requested_documents != decided_documents:
            raise ValueError("submitted documents do not match the approved compliance bundle")
        if runtime.context.facts.get("decided_compliance_review_id") != runtime.context.facts.get(
            "compliance_review_id"
        ):
            raise ValueError("compliance decision does not match the active review")
        bundle = await runtime.context.spike_backend.inspect_document_bundle(case, document_ids)
        if not bundle.get("complete") or not runtime.context.facts.get("reviewed_bundle_complete"):
            raise ValueError("document bundle is incomplete")
        quote = runtime.context.facts.get("customs_quote", {})
        amount = quote.get("supplement_documents", {}).get("amount")
        current_quote = await runtime.context.spike_backend.quote_customs_options(case)
        if current_quote.get("quote_version") != quote.get("quote_version"):
            raise ValueError("customs quote changed; re-quote is required")
        if not isinstance(amount, int) or amount > int(
            runtime.context.facts.get("budget_limit_vnd", 0)
        ):
            raise ValueError("supplement cost exceeds the user's budget")
        result = await runtime.context.spike_backend.submit_customs_documents(
            case,
            document_ids,
            _idempotency(runtime.context, "submit_customs_documents", case),
        )
        if result.get("accepted"):
            runtime.context.facts["customs_submission_id"] = result.get("submission_id")
        return result

    return await _recorded_call(
        runtime,
        "submit_customs_documents",
        {"case_id": case, "document_ids": document_ids},
        operation,
    )


@tool
async def request_return_to_sender(
    case_id: str,
    reason: str,
    runtime: ToolRuntime[SpikeAgentContext],
) -> dict[str, Any]:
    """Request return-to-sender after compliance rejection or budget failure. Requires approval."""
    case = _require_case(runtime.context, case_id)
    if runtime.context.facts.get("compliance_approved") is True:
        raise ValueError("return path is not allowed after compliance approval")
    quote = runtime.context.facts.get("customs_quote", {})
    supplement_amount = quote.get("supplement_documents", {}).get("amount")
    over_budget = isinstance(supplement_amount, int) and supplement_amount > int(
        runtime.context.facts.get("budget_limit_vnd", 0)
    )
    return_allowed = any(
        (
            runtime.context.facts.get("no_valid_battery_report") is True,
            runtime.context.facts.get("compliance_approved") is False,
            over_budget,
        )
    )
    if not return_allowed:
        raise ValueError(
            "return requires invalid documents, budget failure, or compliance rejection"
        )

    async def operation() -> dict[str, Any]:
        result = await runtime.context.spike_backend.request_return_to_sender(
            case,
            reason,
            _idempotency(runtime.context, "return_to_sender", case),
        )
        if result.get("accepted"):
            runtime.context.facts["return_request_id"] = result.get("return_request_id")
        return result

    return await _recorded_call(
        runtime,
        "request_return_to_sender",
        {"case_id": case, "reason": reason},
        operation,
    )


async def _business_result(awaitable: Awaitable[ToolResult]) -> dict[str, Any]:
    return _tool_data(await awaitable)


ORDER_READ_TOOLS = [
    resolve_order_waybills,
    query_tracking,
    query_existing_cases,
    query_pod_evidence,
    check_address_change,
    check_outlet_hold,
    verify_receiver,
]
ORDER_WRITE_TOOLS = [
    submit_address_change,
    submit_delivery_followup,
    submit_missing_delivery_case,
    submit_outlet_hold,
]
CUSTOMS_READ_TOOLS = [
    query_crossborder_case,
    get_customs_declaration,
    retrieve_route_policy,
    inspect_document_bundle,
    quote_customs_options,
    submit_compliance_review,
]
CUSTOMS_WRITE_TOOLS = [submit_customs_documents, request_return_to_sender]
