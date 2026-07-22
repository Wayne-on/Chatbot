from __future__ import annotations

import hashlib
from typing import Any


class MockSpikeBackend:
    """Deterministic multi-system data used only by the DeepAgents spike."""

    ORDER_ID = "ORD-DA-001"
    ORDER_WAYBILLS = ["JT100000001", "JT100000007", "JT100000005", "JT100000003"]
    CUSTOMS_CASE_ID = "CB-VN-CN-001"

    def __init__(self) -> None:
        self._idempotent_results: dict[str, dict[str, Any]] = {}

    async def resolve_order_waybills(self, order_id: str) -> dict[str, Any]:
        if order_id.upper() != self.ORDER_ID:
            return {"order_id": order_id, "found": False, "waybill_nos": []}
        return {
            "order_id": self.ORDER_ID,
            "found": True,
            "waybill_nos": list(self.ORDER_WAYBILLS),
            "recipient_country": "VN",
            "shipment_count": len(self.ORDER_WAYBILLS),
        }

    async def query_existing_cases(self, waybill_no: str) -> dict[str, Any]:
        cases: dict[str, list[dict[str, Any]]] = {
            "JT100000007": [
                {
                    "case_id": "URG-DEMO-1007",
                    "type": "delivery_followup",
                    "status": "closed_without_movement",
                    "age_hours": 28,
                }
            ],
            "JT100000005": [],
            "JT100000001": [],
            "JT100000003": [],
        }
        return {"waybill_no": waybill_no, "cases": cases.get(waybill_no, [])}

    async def query_pod_evidence(self, waybill_no: str) -> dict[str, Any]:
        if waybill_no != "JT100000005":
            return {"waybill_no": waybill_no, "pod_found": False}
        return {
            "waybill_no": waybill_no,
            "pod_found": True,
            "signer": "building security desk",
            "delivery_photo": "POD-PHOTO-1005",
            "courier_gps_distance_m": 176,
            "reception_log_match": False,
            "risk_flags": ["reception_log_mismatch", "gps_not_at_exact_entrance"],
        }

    async def check_outlet_hold(self, waybill_no: str) -> dict[str, Any]:
        return {
            "waybill_no": waybill_no,
            "eligible": waybill_no == "JT100000003",
            "outlet": "District 1 Delivery Outlet",
            "hold_hours": 24,
            "requires_manual_approval": True,
        }

    async def request_outlet_hold(
        self,
        waybill_no: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]
        eligible = (await self.check_outlet_hold(waybill_no))["eligible"]
        result = {
            "accepted": bool(eligible),
            "waybill_no": waybill_no,
            "hold_request_id": self._reference("HOLD", idempotency_key),
            "status": "pending_outlet_review" if eligible else "rejected",
            "reason": reason,
        }
        self._idempotent_results[idempotency_key] = result
        return result

    async def query_crossborder_case(self, case_id: str) -> dict[str, Any]:
        if case_id.upper() != self.CUSTOMS_CASE_ID:
            return {"case_id": case_id, "found": False}
        return {
            "case_id": self.CUSTOMS_CASE_ID,
            "found": True,
            "route": "VN-CN",
            "status": "customs_hold",
            "hold_reason": "restricted-item documents incomplete",
            "state_version": 3,
            "forced_return": False,
        }

    async def get_customs_declaration(self, case_id: str) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "declared_items": [
                {"category": "cosmetic_liquid", "quantity": 3},
                {"category": "lithium_battery", "quantity": 2},
                {"category": "coffee", "quantity": 4},
            ],
            "declared_value_vnd": 2_300_000,
            "documents_on_file": ["DOC-INVOICE-001"],
        }

    async def retrieve_route_policy(self, route: str) -> dict[str, Any]:
        return {
            "route": route,
            "policy_version": "VN-CN-2026.04",
            "policy_ids": ["POL-BAT-UN3481", "POL-LIQ-COS-02", "POL-FOOD-COF-01"],
            "requirements": {
                "lithium_battery": ["valid_battery_test_report"],
                "cosmetic_liquid": ["ingredient_statement"],
                "coffee": ["commercial_invoice"],
            },
            "partial_removal_supported": False,
            "human_review_required": True,
            "disclaimer": "Mock policy for framework evaluation only.",
        }

    async def inspect_document_bundle(
        self, case_id: str, document_ids: list[str]
    ) -> dict[str, Any]:
        catalog = {
            "DOC-INVOICE-001": {
                "type": "commercial_invoice",
                "valid": True,
                "reason": "invoice fields complete",
            },
            "DOC-BAT-VALID": {
                "type": "battery_test_report",
                "valid": True,
                "reason": "report valid through 2027-03-31",
            },
            "DOC-BAT-EXPIRED": {
                "type": "battery_test_report",
                "valid": False,
                "reason": "report expired on 2025-12-31",
            },
            "DOC-LIQ-VALID": {
                "type": "ingredient_statement",
                "valid": True,
                "reason": "ingredients and concentration present",
            },
        }
        inspected = [
            {
                "document_id": item,
                **catalog.get(
                    item, {"type": "unknown", "valid": False, "reason": "unknown document"}
                ),
            }
            for item in document_ids
        ]
        valid_types = {item["type"] for item in inspected if item["valid"]}
        required = {"commercial_invoice", "battery_test_report", "ingredient_statement"}
        return {
            "case_id": case_id,
            "documents": inspected,
            "complete": required.issubset(valid_types),
            "missing_types": sorted(required - valid_types),
        }

    async def quote_customs_options(self, case_id: str) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "currency": "VND",
            "quote_version": "QUOTE-2026-07-21-A",
            "supplement_documents": {"amount": 420_000, "valid_for_minutes": 30},
            "return_to_sender": {"amount": 260_000, "valid_for_minutes": 30},
            "partial_removal": {"available": False},
        }

    async def submit_compliance_review(
        self, case_id: str, document_ids: list[str]
    ) -> dict[str, Any]:
        bundle = await self.inspect_document_bundle(case_id, document_ids)
        review_key = f"{case_id}|{'|'.join(sorted(document_ids))}"
        return {
            "case_id": case_id,
            "review_id": self._reference("REV", review_key),
            "status": "waiting_human_decision",
            "bundle_complete": bundle["complete"],
            "review_hint": "approve" if bundle["complete"] else "reject",
        }

    async def submit_customs_documents(
        self,
        case_id: str,
        document_ids: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]
        bundle = await self.inspect_document_bundle(case_id, document_ids)
        result = {
            "accepted": bool(bundle["complete"]),
            "case_id": case_id,
            "submission_id": self._reference("CUS", idempotency_key),
            "status": "submitted_to_customs" if bundle["complete"] else "rejected_incomplete",
            "document_ids": document_ids,
        }
        self._idempotent_results[idempotency_key] = result
        return result

    async def request_return_to_sender(
        self,
        case_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if idempotency_key in self._idempotent_results:
            return self._idempotent_results[idempotency_key]
        result = {
            "accepted": True,
            "case_id": case_id,
            "return_request_id": self._reference("RTS", idempotency_key),
            "status": "return_requested",
            "reason": reason,
        }
        self._idempotent_results[idempotency_key] = result
        return result

    @staticmethod
    def _reference(prefix: str, raw: str) -> str:
        return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10].upper()
