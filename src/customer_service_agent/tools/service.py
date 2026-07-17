from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from customer_service_agent.adapters.base import BusinessBackend
from customer_service_agent.exceptions import BackendError
from customer_service_agent.schemas import RequestContext, ToolResult, ToolStatus
from customer_service_agent.tools.complaint import CreateComplaintInput, QueryComplaintInput
from customer_service_agent.tools.delivery import (
    ChangeAddressInput,
    CheckAddressChangeInput,
    UrgeDeliveryInput,
)
from customer_service_agent.tools.identity import VerifyReceiverInput
from customer_service_agent.tools.knowledge import RetrieveFAQInput, TransferToHumanInput
from customer_service_agent.tools.tracking import QueryPackageVolumeInput, QueryTrackingInput

logger = logging.getLogger(__name__)


class BusinessTools:
    """Validated Tool boundary. It is the only caller of BusinessBackend methods."""

    def __init__(self, backend: BusinessBackend) -> None:
        self.backend = backend

    async def _call(
        self,
        tool_name: str,
        context: RequestContext,
        operation: Callable[[], Awaitable[dict[str, Any]]],
    ) -> ToolResult:
        started = perf_counter()
        try:
            data = await operation()
            result = ToolResult(
                status=ToolStatus.SUCCESS,
                data=data,
                message="ok",
                trace_id=context.trace_id,
            )
        except BackendError as exc:
            result = ToolResult(
                status=ToolStatus.FAILED,
                error_code=exc.error_code,
                message=str(exc),
                retryable=exc.retryable,
                trace_id=context.trace_id,
            )
        except Exception:
            logger.exception(
                "unexpected tool failure tool_name=%s trace_id=%s", tool_name, context.trace_id
            )
            result = ToolResult(
                status=ToolStatus.FAILED,
                error_code="INTERNAL_TOOL_ERROR",
                message="The business service is temporarily unavailable.",
                retryable=False,
                trace_id=context.trace_id,
            )
        latency_ms = round((perf_counter() - started) * 1000, 2)
        logger.info(
            "tool_call trace_id=%s tool_name=%s tool_latency_ms=%s tool_status=%s error_code=%s",
            context.trace_id,
            tool_name,
            latency_ms,
            result.status,
            result.error_code,
        )
        return result

    async def query_tracking(self, args: QueryTrackingInput, context: RequestContext) -> ToolResult:
        return await self._call(
            "query_tracking", context, lambda: self.backend.query_tracking(args.waybill_no, context)
        )

    async def query_package_volume(
        self, args: QueryPackageVolumeInput, context: RequestContext
    ) -> ToolResult:
        return await self._call(
            "query_package_volume",
            context,
            lambda: self.backend.query_package_volume(args.waybill_no, context),
        )

    async def verify_receiver(
        self, args: VerifyReceiverInput, context: RequestContext
    ) -> ToolResult:
        return await self._call(
            "verify_receiver",
            context,
            lambda: self.backend.verify_receiver(args.waybill_no, args.phone_last4, context),
        )

    async def urge_delivery(self, args: UrgeDeliveryInput, context: RequestContext) -> ToolResult:
        return await self._call(
            "urge_delivery",
            context,
            lambda: self.backend.urge_delivery(
                args.waybill_no, args.reason, args.idempotency_key, context
            ),
        )

    async def create_complaint(
        self, args: CreateComplaintInput, context: RequestContext
    ) -> ToolResult:
        return await self._call(
            "create_complaint",
            context,
            lambda: self.backend.create_complaint(
                args.waybill_no,
                args.complaint_type,
                args.description,
                args.idempotency_key,
                context,
            ),
        )

    async def query_complaint(
        self, args: QueryComplaintInput, context: RequestContext
    ) -> ToolResult:
        return await self._call(
            "query_complaint",
            context,
            lambda: self.backend.query_complaint(args.ticket_id, context),
        )

    async def check_address_change(
        self, args: CheckAddressChangeInput, context: RequestContext
    ) -> ToolResult:
        return await self._call(
            "check_address_change",
            context,
            lambda: self.backend.check_address_change(args.waybill_no, context),
        )

    async def change_address(self, args: ChangeAddressInput, context: RequestContext) -> ToolResult:
        return await self._call(
            "change_address",
            context,
            lambda: self.backend.change_address(
                args.waybill_no, args.new_address, args.idempotency_key, context
            ),
        )

    async def retrieve_faq(self, args: RetrieveFAQInput, context: RequestContext) -> ToolResult:
        return await self._call(
            "retrieve_faq",
            context,
            lambda: self.backend.retrieve_faq(args.query, args.language, context),
        )

    async def transfer_to_human(
        self, args: TransferToHumanInput, context: RequestContext
    ) -> ToolResult:
        return await self._call(
            "transfer_to_human",
            context,
            lambda: self.backend.transfer_to_human(args.reason, context),
        )
