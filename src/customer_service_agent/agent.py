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

logger = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
SKILLS_ROOT = PACKAGE_ROOT / "skills"


class CustomerServiceAgent:
    """Unified API-facing agent entry with an optional DeepAgents runtime."""

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
        self._model_unavailable_until = 0.0
        self.response_model: Any | None = None
        self.deep_agent: Any | None = None
        if settings.model_enabled:
            self.response_model = self._build_model()
            self.deep_agent = self._build_deep_agent(self.response_model)
        if self.deep_agent is not None:
            self.service.model_router = self._route_with_deep_agent
            self.service.response_generator = self._generate_final_reply

    async def ainvoke(self, request: ChatRequest, *, trace_id: str | None = None) -> ChatResponse:
        # The explicit state machine remains authoritative for business execution. The optional
        # DeepAgents runtime is available for model-enhanced understanding without changing this API.
        return await self.service.handle(request, trace_id=trace_id)

    async def _route_with_deep_agent(
        self,
        message: str,
        state: ConversationState,
        context: RequestContext,
    ) -> RouteDecision | None:
        if self.deep_agent is None or self._model_in_cooldown():
            return None
        started = perf_counter()
        try:
            understanding: ModelUnderstanding | None = None
            result: dict[str, Any] = {}
            state_context = self._safe_conversation_context(state)
            for attempt in range(2):
                messages = [
                    {"role": item.role, "content": item.content}
                    for item in state.recent_messages
                ]
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Plan the current logistics customer-service turn and return only the "
                            "required structured semantic decision. Use the preceding messages as "
                            "real dialogue history. Reuse known identifiers unless corrected. "
                            f"Verified business state: "
                            f"{json.dumps(state_context, ensure_ascii=False)}\n"
                            f"Current user message: {message}"
                        ),
                    }
                )
                result = await self.deep_agent.ainvoke({"messages": messages})
                try:
                    structured = result.get("structured_response")
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
            usage = self._usage_from_result(result)
            logger.info(
                "model_call trace_id=%s model_latency_ms=%.2f input_tokens=%s "
                "output_tokens=%s total_tokens=%s",
                context.trace_id,
                (perf_counter() - started) * 1000,
                usage["input_tokens"],
                usage["output_tokens"],
                usage["total_tokens"],
            )
            self._model_unavailable_until = 0.0
            return RouteDecision(
                intent=understanding.intent,
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
            self._mark_model_unavailable()
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
        if self.response_model is None or self._model_in_cooldown():
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
                {"role": item.role, "content": item.content}
                for item in state.recent_messages
            )
            payload = {
                "language": state.language,
                "current_intent": (
                    state.current_intent.value if state.current_intent else None
                ),
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
            self._model_unavailable_until = 0.0
            return reply
        except Exception:
            self._mark_model_unavailable()
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

    def _build_deep_agent(self, model: Any) -> Any:
        from deepagents import FilesystemPermission, create_deep_agent
        from deepagents.backends import StoreBackend
        from langchain.agents.structured_output import ToolStrategy
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()
        skills_backend = StoreBackend(
            store=store,
            namespace=lambda _runtime: ("customer-service-agent",),
        )
        skill_files = [
            (f"/skills/{path.relative_to(SKILLS_ROOT).as_posix()}", path.read_bytes())
            for path in SKILLS_ROOT.rglob("*")
            if path.is_file()
        ]
        uploaded = skills_backend.upload_files(skill_files)
        if any(item.error for item in uploaded):
            raise RuntimeError("failed to seed one or more DeepAgents Skill files")
        agent = create_deep_agent(
            model=model,
            # Semantic planning is read-only. Business Tools execute exactly once in the service.
            tools=[],
            system_prompt=f"{MAIN_AGENT_PROMPT}\n\n{SEMANTIC_SKILL_CATALOG}",
            backend=skills_backend,
            store=store,
            # The compact catalog avoids multi-call Skill file browsing during classification.
            # The selected full SKILL.md is still loaded for grounded final-response generation.
            skills=None,
            permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],
            response_format=ToolStrategy(ModelUnderstanding),
        )
        logger.info("DeepAgents runtime initialized with skills_root=%s", SKILLS_ROOT)
        return agent

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

    def _model_in_cooldown(self) -> bool:
        return perf_counter() < self._model_unavailable_until

    def _mark_model_unavailable(self) -> None:
        self._model_unavailable_until = (
            perf_counter() + self.settings.model_failure_cooldown_seconds
        )

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
    def _usage_from_result(result: dict[str, Any]) -> dict[str, int]:
        totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for message in result.get("messages", []):
            usage = getattr(message, "usage_metadata", None) or {}
            totals["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
            totals["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
            totals["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        return totals

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
            "language": state.language,
            "known_slots": {
                "waybill_no": state.slots.get("waybill_no") or state.last_waybill_no,
                "ticket_id": state.slots.get("ticket_id") or state.last_ticket_id,
                "complaint_type": state.slots.get("complaint_type"),
            },
            "waybill_history": list(state.waybill_history),
            "valid_waybill_history": list(state.valid_waybill_history),
            "ticket_history": list(state.ticket_history),
            "last_tool_result": {key: result.get(key) for key in safe_result_keys if key in result},
        }
