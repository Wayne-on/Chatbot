from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from customer_service_agent.exceptions import BackendError, BackendTimeoutError
from customer_service_agent.middleware.resilience import retry_query
from customer_service_agent.schemas import RequestContext


class HttpBackend:
    """Open contract for future internal APIs; endpoint mappings are intentionally isolated here."""

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str | None,
        query_max_retries: int = 2,
        timeout: float = 10.0,
    ) -> None:
        headers = {"Accept": "application/json"}
        if service_token:
            headers["X-Service-Token"] = service_token
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout)
        self._query_max_retries = max(1, query_max_retries)

    @staticmethod
    def _headers(context: RequestContext, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "X-Request-ID": context.request_id,
            "X-Trace-ID": context.trace_id,
            "X-User-ID": context.user_id,
        }
        if context.user_credential:
            headers["Authorization"] = f"Bearer {context.user_credential.get_secret_value()}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        context: RequestContext,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json,
                headers=self._headers(context, idempotency_key),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise BackendError("business API returned a non-object response")
            return payload
        except httpx.TimeoutException as exc:
            raise BackendTimeoutError() from exc
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code >= 500
            raise BackendError(
                "business API rejected the request",
                error_code=f"BUSINESS_HTTP_{exc.response.status_code}",
                retryable=retryable,
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise BackendError(str(exc), retryable=True) from exc

    async def _query(
        self,
        path: str,
        *,
        context: RequestContext,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await retry_query(
            lambda: self._request("GET", path, context=context, params=params),
            max_attempts=self._query_max_retries,
        )

    async def _write(
        self,
        path: str,
        *,
        context: RequestContext,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "POST",
                path,
                context=context,
                json=payload,
                idempotency_key=idempotency_key,
            )
        except BackendTimeoutError:
            # Never repeat a write blindly. Ask the backend for the idempotent result once.
            return await self._query(
                f"/v1/idempotency/{quote(idempotency_key, safe='')}",
                context=context,
            )

    async def query_tracking(self, waybill_no: str, context: RequestContext) -> dict[str, Any]:
        return await self._query(f"/v1/shipments/{quote(waybill_no)}/tracking", context=context)

    async def query_package_volume(
        self, waybill_no: str, context: RequestContext
    ) -> dict[str, Any]:
        return await self._query(f"/v1/shipments/{quote(waybill_no)}/volume", context=context)

    async def verify_receiver(
        self, waybill_no: str, phone_last4: str, context: RequestContext
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/shipments/{quote(waybill_no)}/verify-receiver",
            context=context,
            json={"phone_last4": phone_last4},
        )

    async def urge_delivery(
        self,
        waybill_no: str,
        reason: str,
        idempotency_key: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        return await self._write(
            "/v1/delivery-followups",
            context=context,
            payload={"waybill_no": waybill_no, "reason": reason},
            idempotency_key=idempotency_key,
        )

    async def create_complaint(
        self,
        waybill_no: str,
        complaint_type: str,
        description: str,
        idempotency_key: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        return await self._write(
            "/v1/complaints",
            context=context,
            payload={
                "waybill_no": waybill_no,
                "complaint_type": complaint_type,
                "description": description,
            },
            idempotency_key=idempotency_key,
        )

    async def query_complaint(self, ticket_id: str, context: RequestContext) -> dict[str, Any]:
        return await self._query(f"/v1/complaints/{quote(ticket_id)}", context=context)

    async def check_address_change(
        self, waybill_no: str, context: RequestContext
    ) -> dict[str, Any]:
        return await self._query(
            f"/v1/shipments/{quote(waybill_no)}/address-change-eligibility",
            context=context,
        )

    async def change_address(
        self,
        waybill_no: str,
        new_address: str,
        idempotency_key: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        return await self._write(
            f"/v1/shipments/{quote(waybill_no)}/address-changes",
            context=context,
            payload={"new_address": new_address},
            idempotency_key=idempotency_key,
        )

    async def retrieve_faq(
        self, query: str, language: str, context: RequestContext
    ) -> dict[str, Any]:
        return await self._query(
            "/v1/knowledge/search",
            context=context,
            params={"q": query, "language": language, "country": "VN", "channel": "app"},
        )

    async def transfer_to_human(self, reason: str, context: RequestContext) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/human-transfers",
            context=context,
            json={"reason": reason, "session_id": context.session_id},
        )

    async def ready(self) -> bool:
        try:
            response = await self._client.get("/ready")
            return response.is_success
        except httpx.RequestError:
            return False
