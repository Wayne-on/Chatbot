from __future__ import annotations

import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from customer_service_agent.config import Settings
from customer_service_agent.prompts import (
    FINAL_RESPONSE_PROMPT,
    MAIN_AGENT_PROMPT,
    SEMANTIC_SKILL_CATALOG,
)
from customer_service_agent.router import normalize_waybill
from customer_service_agent.schemas import (
    ChatRequest,
    ChatResponse,
    Intent,
    ModelUnderstanding,
    RequestContext,
    RouteDecision,
    SceneStatus,
)
from customer_service_agent.services.conversation_service import ConversationService
from customer_service_agent.state import ConversationState
from customer_service_agent.tools.service import BusinessTools
from customer_service_agent.workflow import CustomerServiceWorkflow

logger = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
SKILLS_ROOT = PACKAGE_ROOT / "skills"


class CustomerServiceAgent:
    """Unified API entry backed by an explicit outer LangGraph workflow."""

    def __init__(
        self,
        *,
        service: ConversationService,
        settings: Settings,
        tools: BusinessTools,
    ) -> None:
        self.service = service
        self.settings = settings
        self.tools = tools
        self._routing_unavailable_until = 0.0
        self._response_unavailable_until = 0.0
        self.response_model: Any | None = None
        self.semantic_model: Any | None = None
        if settings.model_enabled:
            self.response_model = self._build_model()
            self.semantic_model = self._build_semantic_model(self.response_model)
        if self.semantic_model is not None:
            self.service.model_router = self._route_with_langgraph_model
            self.service.response_generator = self._generate_final_reply
        self.workflow = CustomerServiceWorkflow(service)
        self.graph = self.workflow.graph

    async def ainvoke(self, request: ChatRequest, *, trace_id: str | None = None) -> ChatResponse:
        return await self.workflow.ainvoke(request, trace_id=trace_id)

    async def _route_with_langgraph_model(
        self,
        message: str,
        state: ConversationState,
        context: RequestContext,
    ) -> RouteDecision | None:
        if self.semantic_model is None or self._model_in_cooldown("routing"):
            return None
        started = perf_counter()
        try:
            understanding: ModelUnderstanding | None = None
            raw_message: Any | None = None
            state_context = self._safe_conversation_context(state)
            deterministic_hint = self.service.router.route(
                message,
                requested_language=None,
                state=state,
            )
            hint_context = {
                "intent": (deterministic_hint.intent.value if deterministic_hint.intent else None),
                "secondary_intents": [
                    intent.value for intent in deterministic_hint.secondary_intents
                ],
                "language": deterministic_hint.language,
                "waybill_no": deterministic_hint.waybill_no,
                "invalid_waybill_no": deterministic_hint.invalid_waybill_no,
                "phone_last4": deterministic_hint.phone_last4,
                "ticket_id": deterministic_hint.ticket_id,
                "confirmation": deterministic_hint.confirmation,
                "rejection": deterministic_hint.rejection,
                "cancel_requested": deterministic_hint.cancel_requested,
                "human_requested": deterministic_hint.human_requested,
                "semantic_conflict": deterministic_hint.semantic_conflict,
            }
            for attempt in range(2):
                messages: list[dict[str, str]] = [
                    {
                        "role": "system",
                        "content": f"{MAIN_AGENT_PROMPT}\n\n{SEMANTIC_SKILL_CATALOG}",
                    }
                ]
                messages.extend(
                    {"role": item.role, "content": item.content} for item in state.recent_messages
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Plan the current logistics customer-service turn and return only the "
                            "required structured semantic decision. Use the preceding messages as "
                            "real dialogue history. Reuse known identifiers unless corrected. "
                            f"Verified business state: "
                            f"{json.dumps(state_context, ensure_ascii=False)}\n"
                            "Deterministic extraction hint (identifiers are authoritative; "
                            "semantic intent is only a hint): "
                            f"{json.dumps(hint_context, ensure_ascii=False)}\n"
                            "Country/Region: VN\nChannel: app\n"
                            f"Current user message: {message}"
                        ),
                    }
                )
                result = await self.semantic_model.ainvoke(messages)
                try:
                    if isinstance(result, dict) and "parsed" in result:
                        raw_message = result.get("raw")
                        if result.get("parsing_error") is not None:
                            if attempt == 0:
                                logger.warning(
                                    "model_route_parse_retry trace_id=%s", context.trace_id
                                )
                                continue
                            raise result["parsing_error"]
                        structured = result.get("parsed")
                    else:
                        structured = result
                    understanding = (
                        structured
                        if isinstance(structured, ModelUnderstanding)
                        else ModelUnderstanding.model_validate(structured)
                    )
                    break
                except ValidationError:
                    if attempt == 0:
                        logger.warning("model_route_parse_retry trace_id=%s", context.trace_id)
                        continue
                    raise
            assert understanding is not None
            usage = getattr(raw_message, "usage_metadata", None) or {}
            logger.info(
                "model_call trace_id=%s model_latency_ms=%.2f input_tokens=%s "
                "output_tokens=%s total_tokens=%s",
                context.trace_id,
                (perf_counter() - started) * 1000,
                int(usage.get("input_tokens", 0) or 0),
                int(usage.get("output_tokens", 0) or 0),
                int(usage.get("total_tokens", 0) or 0),
            )
            self._routing_unavailable_until = 0.0
            return RouteDecision(
                intent=understanding.intent,
                secondary_intents=understanding.secondary_intents,
                intent_relation=understanding.intent_relation,
                intent_condition=understanding.intent_condition,
                language=understanding.language,
                waybill_no=(
                    normalize_waybill(understanding.waybill_no)
                    if understanding.waybill_no
                    else None
                ),
                phone_last4=(
                    understanding.phone_last4
                    if understanding.phone_last4
                    and understanding.phone_last4.isdigit()
                    and len(understanding.phone_last4) == 4
                    else None
                ),
                ticket_id=understanding.ticket_id,
                new_address=understanding.new_address,
                cancel_requested=understanding.cancel_requested,
                human_requested=understanding.human_requested,
                modifies_existing=understanding.modifies_existing,
                continuation=understanding.continuation,
                confidence=understanding.confidence,
                clarify_question=understanding.clarify_question,
                business_reason=understanding.business_reason,
                recommended_tool=understanding.recommended_tool,
                explicit_intent=not understanding.continuation,
            )
        except Exception:
            self._mark_model_unavailable("routing")
            logger.exception(
                "model_routing_failed trace_id=%s model_latency_ms=%.2f",
                context.trace_id,
                (perf_counter() - started) * 1000,
            )
            return None

    async def _generate_final_reply(
        self,
        message: str,
        state: ConversationState,
        response: ChatResponse,
        context: RequestContext,
    ) -> str | None:
        """Compose a grounded reply after deterministic execution, like the DSL final LLM node."""
        if self.response_model is None or self._model_in_cooldown("response"):
            return None
        if response.status != SceneStatus.COMPLETED or response.action_required is not None:
            return None

        started = perf_counter()
        try:
            skill_text = self._skill_text(state.current_intent)
            system_prompt = FINAL_RESPONSE_PROMPT
            if skill_text:
                system_prompt += f"\n\nRelevant workflow requirements:\n{skill_text}"
            messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
            messages.extend(
                {"role": item.role, "content": item.content} for item in state.recent_messages
            )
            payload = {
                "language": state.language,
                "current_intent": (state.current_intent.value if state.current_intent else None),
                "current_user_message": message,
                "business_state": self._safe_conversation_context(state),
                "verified_response_data": response.data,
                "deterministic_safe_draft": response.reply,
            }
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Write the final reply from this verified turn payload:\n"
                        + json.dumps(payload, ensure_ascii=False)
                    ),
                }
            )
            generated = await self.response_model.ainvoke(messages)
            reply = self._plain_text_reply(self._message_text(generated))
            if not reply or len(reply) > 4000:
                return None

            # Identifiers returned by a business operation must survive stylistic rewriting.
            required_ids = self._required_identifiers(response.data, response.reply)
            if any(identifier not in reply for identifier in required_ids):
                logger.warning(
                    "model_reply_rejected_missing_identifier trace_id=%s", context.trace_id
                )
                return None
            usage = getattr(generated, "usage_metadata", None) or {}
            logger.info(
                "model_reply trace_id=%s model_latency_ms=%.2f input_tokens=%s "
                "output_tokens=%s total_tokens=%s",
                context.trace_id,
                (perf_counter() - started) * 1000,
                int(usage.get("input_tokens", 0) or 0),
                int(usage.get("output_tokens", 0) or 0),
                int(usage.get("total_tokens", 0) or 0),
            )
            self._response_unavailable_until = 0.0
            return reply
        except Exception:
            self._mark_model_unavailable("response")
            logger.exception(
                "model_reply_failed trace_id=%s model_latency_ms=%.2f",
                context.trace_id,
                (perf_counter() - started) * 1000,
            )
            return None

    def _build_model(self) -> Any:
        from langchain_openai import ChatOpenAI

        api_key = self.settings.effective_model_api_key
        assert api_key is not None
        extra_body = None
        if "deepseek.com" in (self.settings.model_base_url or ""):
            extra_body = {
                "thinking": {
                    "type": "enabled" if self.settings.model_thinking_enabled else "disabled"
                }
            }
        return ChatOpenAI(
            model=self.settings.model_name or "",
            api_key=api_key.get_secret_value(),
            base_url=self.settings.model_base_url,
            temperature=self.settings.model_temperature,
            timeout=self.settings.model_timeout,
            max_retries=self.settings.model_max_retries,
            extra_body=extra_body,
        )

    @staticmethod
    def _build_semantic_model(model: Any) -> Any:
        semantic_model = model.with_structured_output(
            ModelUnderstanding,
            method="function_calling",
            include_raw=True,
        )
        logger.info("LangGraph semantic model initialized with skills_root=%s", SKILLS_ROOT)
        return semantic_model

    @staticmethod
    def _message_text(message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return str(content)

    @staticmethod
    def _plain_text_reply(content: str) -> str:
        """Remove common presentational Markdown that the plain chat UI would show literally."""
        return content.strip().replace("**", "").replace("__", "").replace("`", "")

    def _model_in_cooldown(self, channel: str) -> bool:
        unavailable_until = (
            self._routing_unavailable_until
            if channel == "routing"
            else self._response_unavailable_until
        )
        return perf_counter() < unavailable_until

    def _mark_model_unavailable(self, channel: str) -> None:
        unavailable_until = perf_counter() + self.settings.model_failure_cooldown_seconds
        if channel == "routing":
            self._routing_unavailable_until = unavailable_until
        else:
            self._response_unavailable_until = unavailable_until

    @staticmethod
    def _required_identifiers(data: dict[str, Any], deterministic_draft: str) -> set[str]:
        identifiers: set[str] = set()

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if (
                        key in {"waybill_no", "ticket_id", "request_id"}
                        and child
                        and str(child) in deterministic_draft
                    ):
                        identifiers.add(str(child))
                    else:
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(data)
        return identifiers

    @staticmethod
    def _skill_text(intent: Intent | None) -> str:
        skill_names = {
            Intent.TRACKING: "tracking",
            Intent.PACKAGE_VOLUME: "query-package-volume",
            Intent.DELIVERY_FOLLOWUP: "delivery-followup",
            Intent.DELIVERED_NOT_RECEIVED: "delivered-not-received",
            Intent.CHANGE_ADDRESS: "change-address",
            Intent.COMPLAINT: "complaint",
            Intent.QUERY_COMPLAINT: "complaint",
            Intent.FAQ: "faq",
            Intent.CONVERSATION: "fallback",
            Intent.FALLBACK: "fallback",
        }
        name = skill_names.get(intent)
        if not name:
            return ""
        path = SKILLS_ROOT / name / "SKILL.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    @staticmethod
    def _safe_conversation_context(state: ConversationState) -> dict[str, Any]:
        result = state.last_tool_result or {}
        safe_result_keys = (
            "waybill_no",
            "status",
            "current_node",
            "can_urge",
            "eta",
            "exception",
            "ticket_id",
            "complaint_type",
            "found",
            "length_cm",
            "width_cm",
            "height_cm",
            "volume_cm3",
        )
        return {
            "current_intent": state.current_intent.value if state.current_intent else None,
            "current_step": state.current_step,
            "scene_status": state.scene_status.value,
            "pending_intents": [
                {
                    "intent": item.intent.value,
                    "relation": item.relation.value,
                    "condition": item.condition,
                }
                for item in state.pending_intents
            ],
            "language": state.language,
            "known_slots": {
                "waybill_no": state.slots.get("waybill_no") or state.last_waybill_no,
                "ticket_id": state.slots.get("ticket_id") or state.last_ticket_id,
                "complaint_type": state.slots.get("complaint_type"),
            },
            "waybill_history": list(state.waybill_history),
            "valid_waybill_history": list(state.valid_waybill_history),
            "ticket_history": list(state.ticket_history),
            "last_business_reason": state.last_business_reason,
            "last_tool_result": {key: result.get(key) for key in safe_result_keys if key in result},
        }
