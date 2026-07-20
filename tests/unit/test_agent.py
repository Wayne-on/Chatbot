from customer_service_agent.agent import CustomerServiceAgent
from customer_service_agent.api.dependencies import build_container
from customer_service_agent.config import Settings
from customer_service_agent.schemas import Intent, ModelUnderstanding, RequestContext, SceneStatus
from customer_service_agent.state import ConversationState


def test_optional_langgraph_runtime_builds_without_network_call() -> None:
    container = build_container(
        Settings(
            model_name="gpt-4o-mini",
            model_api_key="sk-test-not-used",
            model_base_url="https://example.invalid/v1",
        )
    )
    assert container.agent.semantic_model is not None
    assert type(container.agent.graph).__name__ == "CompiledStateGraph"
    assert set(container.agent.graph.get_graph().nodes) >= {
        "load_session",
        "deterministic_router",
        "semantic_router",
        "resolve_decision",
        "execute_business",
        "response_writer",
        "save_turn",
    }


def test_model_context_contains_business_summary_but_not_sensitive_slots() -> None:
    state = ConversationState(
        current_intent=Intent.TRACKING,
        current_step="completed",
        scene_status=SceneStatus.COMPLETED,
        language="zh",
    )
    state.slots.update(
        {
            "waybill_no": "JT123456781",
            "contact_phone": "84912345678",
            "new_address": "Sensitive address should not be exposed",
        }
    )
    state.last_tool_result = {
        "waybill_no": "JT123456781",
        "status": "in_transit",
        "current_node": "Shanghai Transfer Center",
    }
    state.last_business_reason = "User wants to understand the current delivery delay"
    context = build_container(
        Settings(_env_file=None, model_name=None, model_api_key=None)
    ).agent._safe_conversation_context(state)
    rendered = str(context)
    assert context["known_slots"]["waybill_no"] == "JT123456781"
    assert context["last_tool_result"]["status"] == "in_transit"
    assert context["last_business_reason"] == (
        "User wants to understand the current delivery delay"
    )
    assert "84912345678" not in rendered
    assert "Sensitive address" not in rendered


def test_plain_text_reply_removes_markdown_markers() -> None:
    assert (
        CustomerServiceAgent._plain_text_reply("已创建工单 **CMP1234567890**，请保留 `ticket_id`。")
        == "已创建工单 CMP1234567890，请保留 ticket_id。"
    )


def test_only_identifiers_in_safe_draft_are_required_in_rewrite() -> None:
    data = {"ticket_id": "CMP1234567890", "waybill_no": "JT123456781"}
    assert CustomerServiceAgent._required_identifiers(
        data, "工单 CMP1234567890 当前正在处理中。"
    ) == {"CMP1234567890"}


def test_model_understanding_normalizes_provider_null_strings() -> None:
    understanding = ModelUnderstanding.model_validate(
        {
            "intent": "tracking",
            "language": "zh",
            "waybill_no": "null",
            "phone_last4": "",
            "ticket_id": "None",
            "new_address": "N/A",
            "recommended_tool": "null",
        }
    )

    assert understanding.waybill_no is None
    assert understanding.phone_last4 is None
    assert understanding.ticket_id is None
    assert understanding.new_address is None
    assert understanding.recommended_tool is None


async def test_model_connection_failure_opens_short_cooldown(container) -> None:
    class FailingSemanticModel:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, payload):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise ConnectionError("offline")

    failing = FailingSemanticModel()
    container.agent.semantic_model = failing
    container.agent.settings.model_failure_cooldown_seconds = 30
    context = RequestContext(
        session_id="cooldown",
        user_id="u1",
        request_id="r1",
        trace_id="t1",
    )

    first = await container.agent._route_with_langgraph_model(
        "ambiguous request", ConversationState(), context
    )
    second = await container.agent._route_with_langgraph_model(
        "another ambiguous request", ConversationState(), context
    )

    assert first is None
    assert second is None
    assert failing.calls == 1
    assert not container.agent._model_in_cooldown("response")


async def test_semantic_model_retries_one_structured_parse_failure(container) -> None:
    class RetryingSemanticModel:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return {
                    "raw": None,
                    "parsed": None,
                    "parsing_error": ValueError("invalid structured output"),
                }
            return {
                "raw": None,
                "parsed": ModelUnderstanding(intent=Intent.TRACKING, language="zh"),
                "parsing_error": None,
            }

    semantic_model = RetryingSemanticModel()
    container.agent.semantic_model = semantic_model
    context = RequestContext(
        session_id="parse-retry",
        user_id="u1",
        request_id="r1",
        trace_id="t1",
    )

    decision = await container.agent._route_with_langgraph_model(
        "查一下快递",
        ConversationState(),
        context,
    )

    assert decision is not None
    assert decision.intent == Intent.TRACKING
    assert semantic_model.calls == 2
