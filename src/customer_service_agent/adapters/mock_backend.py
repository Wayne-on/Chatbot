from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from customer_service_agent.exceptions import BackendTimeoutError
from customer_service_agent.schemas import AuditRecord, RequestContext


class MockBackend:
    """Deterministic in-memory business backend derived from the Dify Code nodes."""

    def __init__(self) -> None:
        self.audit_records: list[AuditRecord] = []
        self._idempotent_results: dict[str, dict[str, Any]] = {}
        self._tickets: dict[str, dict[str, Any]] = {}
        self._failures: dict[str, int] = {}

    def fail_next(self, operation: str, *, times: int = 1) -> None:
        self._failures[operation] = self._failures.get(operation, 0) + times

    def _maybe_fail(self, operation: str) -> None:
        remaining = self._failures.get(operation, 0)
        if remaining > 0:
            self._failures[operation] = remaining - 1
            raise BackendTimeoutError(f"mock timeout in {operation}")

    @staticmethod
    def _bucket(waybill_no: str) -> int:
        digits = [int(char) for char in waybill_no if char.isdigit()]
        return digits[-1] if digits else 0

    @staticmethod
    def _time(hours_delta: int) -> str:
        return (datetime.now(UTC) + timedelta(hours=hours_delta)).isoformat(timespec="minutes")

    def _tracking_profile(self, waybill_no: str) -> dict[str, Any]:
        bucket = self._bucket(waybill_no)
        if bucket in {0, 1, 2}:
            status, node, can_urge = "in_transit", "Shanghai Transfer Center", True
            eta, exception = "Expected at the next station within 1-2 days", None
            events = [
                (-38, "Origin Service Point", "The parcel has been collected"),
                (-26, "Origin Sorting Center", "The parcel has departed"),
                (
                    -8,
                    node,
                    "The parcel has arrived at the transfer center and is waiting for transfer",
                ),
            ]
        elif bucket in {3, 4}:
            status, node, can_urge = "out_for_delivery", "Destination Outlet", True
            eta, exception = "Expected to be delivered today", None
            events = [
                (-30, "Destination Sorting Center", "The parcel arrived in the destination city"),
                (-16, node, "The parcel arrived at the delivery outlet"),
                (-3, node, "The parcel is currently out for delivery"),
            ]
        elif bucket in {5, 6}:
            status, node, can_urge = "delivered", "Destination Outlet", False
            eta, exception = "Delivery has been completed", None
            events = [
                (-36, node, "The parcel arrived at the delivery outlet"),
                (-28, node, "The parcel was out for delivery"),
                (-24, node, "The parcel was delivered. Signer: security/front desk"),
            ]
        elif bucket in {7, 8}:
            status, node, can_urge = "delayed", "East China Sorting Center", True
            eta = "Investigation or delivery follow-up is recommended"
            exception = "Tracking has not updated for more than 48 hours"
            events = [
                (-72, "Origin Service Point", "The parcel has been collected"),
                (-60, "Origin Sorting Center", "The parcel has departed"),
                (
                    -50,
                    node,
                    "The parcel arrived at the sorting center, but no later update is available",
                ),
            ]
        else:
            status, node, can_urge = "exception", "Exception Handling Center", True
            eta = "Manual verification is recommended"
            exception = "Possible address or parcel exception"
            events = [
                (-48, "Destination Sorting Center", "The parcel arrived in the destination city"),
                (-30, node, "The parcel entered the exception-handling process"),
            ]
        event_data = [
            {"time": self._time(age), "node": event_node, "description": description}
            for age, event_node, description in events
        ]
        return {
            "waybill_no": waybill_no,
            "country": "VN",
            "channel": "app",
            "status": status,
            "current_node": node,
            "can_urge": can_urge,
            "eta": eta,
            "exception": exception,
            "events": event_data,
            "pod": (
                {
                    "signed_time": event_data[-1]["time"],
                    "signer": "security/front desk",
                    "pod_type": "electronic_delivery_record",
                }
                if status == "delivered"
                else None
            ),
        }

    async def query_tracking(self, waybill_no: str, context: RequestContext) -> dict[str, Any]:
        self._maybe_fail("query_tracking")
        return self._tracking_profile(waybill_no)

    async def query_package_volume(
        self, waybill_no: str, context: RequestContext
    ) -> dict[str, Any]:
        self._maybe_fail("query_package_volume")
        bucket = self._bucket(waybill_no)
        length = 20 + bucket
        width = 15 + bucket
        height = 10 + bucket
        return {
            "waybill_no": waybill_no,
            "length_cm": length,
            "width_cm": width,
            "height_cm": height,
            "volume_cm3": length * width * height,
            "volumetric_weight_kg": round(length * width * height / 6000, 2),
            "formula": "L*W*H/6000 (demo only)",
        }

    async def verify_receiver(
        self, waybill_no: str, phone_last4: str, context: RequestContext
    ) -> dict[str, Any]:
        self._maybe_fail("verify_receiver")
        verified = phone_last4 == "1234"
        return {
            "waybill_no": waybill_no,
            "verified": verified,
            "reason": None if verified else "receiver verification failed",
        }

    def _audit(
        self,
        *,
        context: RequestContext,
        waybill_no: str | None,
        action: str,
        idempotency_key: str | None,
        status: str,
    ) -> None:
        self.audit_records.append(
            AuditRecord(
                user_id=context.user_id,
                waybill_no=waybill_no,
                request_id=context.request_id,
                action=action,
                timestamp=datetime.now(UTC).isoformat(),
                idempotency_key=idempotency_key,
                result_status=status,
            )
        )

    async def urge_delivery(
        self,
        waybill_no: str,
        reason: str,
        idempotency_key: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        self._maybe_fail("urge_delivery")
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]
        profile = self._tracking_profile(waybill_no)
        if not profile["can_urge"]:
            result = {"accepted": False, "reason": "current status does not support follow-up"}
        else:
            ticket_id = "URG" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:10].upper()
            result = {
                "accepted": True,
                "ticket_id": ticket_id,
                "waybill_no": waybill_no,
                "reason": reason,
                "expected_followup": "The outlet will verify within 24 hours.",
            }
            self._tickets[ticket_id] = result | {"status": "open", "type": "delivery_followup"}
        self._idempotent_results[idempotency_key] = result
        self._audit(
            context=context,
            waybill_no=waybill_no,
            action="urge_delivery",
            idempotency_key=idempotency_key,
            status="success",
        )
        return result

    async def create_complaint(
        self,
        waybill_no: str,
        complaint_type: str,
        description: str,
        idempotency_key: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        self._maybe_fail("create_complaint")
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]
        ticket_id = "CMP" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:10].upper()
        result = {
            "ticket_id": ticket_id,
            "waybill_no": waybill_no,
            "complaint_type": complaint_type,
            "status": "accepted",
            "expected_followup": "Customer service will verify within 24 hours.",
        }
        self._tickets[ticket_id] = result | {"description": description}
        self._idempotent_results[idempotency_key] = result
        self._audit(
            context=context,
            waybill_no=waybill_no,
            action="create_complaint",
            idempotency_key=idempotency_key,
            status="success",
        )
        return result

    async def query_complaint(self, ticket_id: str, context: RequestContext) -> dict[str, Any]:
        self._maybe_fail("query_complaint")
        return self._tickets.get(
            ticket_id,
            {"ticket_id": ticket_id, "found": False, "status": "not_found"},
        )

    async def check_address_change(
        self, waybill_no: str, context: RequestContext
    ) -> dict[str, Any]:
        self._maybe_fail("check_address_change")
        profile = self._tracking_profile(waybill_no)
        can_change = profile["status"] in {"in_transit", "delayed"}
        return {
            "waybill_no": waybill_no,
            "can_change": can_change,
            "current_status": profile["status"],
            "reason": (
                "A change can be attempted before final delivery"
                if can_change
                else "Current logistics status does not support direct address changes"
            ),
        }

    async def change_address(
        self,
        waybill_no: str,
        new_address: str,
        idempotency_key: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        self._maybe_fail("change_address")
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]
        eligibility = await self.check_address_change(waybill_no, context)
        result = {
            "accepted": bool(eligibility["can_change"]),
            "waybill_no": waybill_no,
            "request_id": "ADR" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:10].upper(),
            "message": (
                "Address-change request accepted for outlet verification"
                if eligibility["can_change"]
                else eligibility["reason"]
            ),
        }
        self._idempotent_results[idempotency_key] = result
        self._audit(
            context=context,
            waybill_no=waybill_no,
            action="change_address",
            idempotency_key=idempotency_key,
            status="success",
        )
        return result

    async def retrieve_faq(
        self, query: str, language: str, context: RequestContext
    ) -> dict[str, Any]:
        self._maybe_fail("retrieve_faq")
        folded = query.lower()
        if any(token in folded for token in ("liquid", "battery", "prohibited")) or any(
            token in query for token in ("液体", "电池", "禁寄", "能不能寄")
        ):
            policy = "prohibited_items"
            answers = {
                "zh": "禁寄和限制寄递规则会因地区、渠道及物品类型而变化；液体、电池、食品、粉末和药品通常需要进一步核实，最终以当地网点或系统校验为准。",
                "vi": "Quy định hàng cấm/hạn chế phụ thuộc khu vực, kênh và loại hàng; chất lỏng, pin, thực phẩm, bột và thuốc cần được xác minh tại bưu cục.",
                "en": "Restricted-item rules vary by region, channel and item type. Liquids, batteries, food, powders and medicines require local acceptance checks.",
            }
        elif any(token in folded for token in ("how long", "delivery time", "eta")) or any(
            token in query for token in ("多久", "时效", "什么时候到")
        ):
            policy = "delivery_time"
            answers = {
                "zh": "时效会受始发地、目的地、天气、中转、清关和末端派送影响；如有运单号，建议先查实时轨迹。",
                "vi": "Thời gian giao phụ thuộc nơi gửi/nhận, thời tiết, trung chuyển, thông quan và giao chặng cuối; nếu có mã vận đơn, hãy tra cứu tracking trước.",
                "en": "Delivery time depends on origin, destination, weather, transit, customs and last-mile delivery. Check live tracking first when a waybill is available.",
            }
        else:
            policy = "general"
            answers = {
                "zh": "目前只有示例政策资料；具体规则需要以越南当地网点或人工客服核实结果为准。",
                "vi": "Hiện chỉ có dữ liệu chính sách minh họa; vui lòng xác minh quy định cụ thể với bưu cục hoặc nhân viên tại Việt Nam.",
                "en": "Only demo policy data is available; specific rules must be confirmed with the local Vietnam outlet or a human agent.",
            }
        return {
            "matched_policy": policy,
            "answer": answers.get(language, answers["en"]),
            "country": "VN",
        }

    async def transfer_to_human(self, reason: str, context: RequestContext) -> dict[str, Any]:
        self._maybe_fail("transfer_to_human")
        queue_id = "HUM" + hashlib.sha256(context.request_id.encode()).hexdigest()[:8].upper()
        self._audit(
            context=context,
            waybill_no=None,
            action="transfer_to_human",
            idempotency_key=None,
            status="success",
        )
        return {"queue_id": queue_id, "reason": reason, "status": "queued"}

    async def ready(self) -> bool:
        return True
