from __future__ import annotations

from typing import Any, Protocol

from customer_service_agent.schemas import RequestContext


class BusinessBackend(Protocol):
    async def query_tracking(self, waybill_no: str, context: RequestContext) -> dict[str, Any]: ...

    async def query_package_volume(
        self, waybill_no: str, context: RequestContext
    ) -> dict[str, Any]: ...

    async def verify_receiver(
        self, waybill_no: str, phone_last4: str, context: RequestContext
    ) -> dict[str, Any]: ...

    async def urge_delivery(
        self,
        waybill_no: str,
        reason: str,
        idempotency_key: str,
        context: RequestContext,
    ) -> dict[str, Any]: ...

    async def create_complaint(
        self,
        waybill_no: str,
        complaint_type: str,
        description: str,
        idempotency_key: str,
        context: RequestContext,
    ) -> dict[str, Any]: ...

    async def query_complaint(self, ticket_id: str, context: RequestContext) -> dict[str, Any]: ...

    async def check_address_change(
        self, waybill_no: str, context: RequestContext
    ) -> dict[str, Any]: ...

    async def change_address(
        self,
        waybill_no: str,
        new_address: str,
        idempotency_key: str,
        context: RequestContext,
    ) -> dict[str, Any]: ...

    async def retrieve_faq(
        self, query: str, language: str, context: RequestContext
    ) -> dict[str, Any]: ...

    async def transfer_to_human(self, reason: str, context: RequestContext) -> dict[str, Any]: ...

    async def ready(self) -> bool: ...
