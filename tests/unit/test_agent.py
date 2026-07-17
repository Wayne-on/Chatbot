from customer_service_agent.agent import CustomerServiceAgent
from customer_service_agent.api.dependencies import build_container
from customer_service_agent.config import Settings
from customer_service_agent.schemas import Intent, RequestContext, SceneStatus
from customer_service_agent.state import ConversationState


def test_optional_deepagents_runtime_builds_without_network_call() -> None:
    container = build_container(
        Settings(
            model_name="gpt-4o-mini",
            model_api_key="sk-test-not-used",
            model_base_url="https://example.invalid/v1",
        )
    )
    assert container.agent.deep_agent is not None
    assert type(container.agent.deep_agent).__name__ == "CompiledStateGraph"


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
    context = build_container(
        Settings(_env_file=None, model_name=None, model_api_key=None)
    ).agent._safe_conversation_context(state)
    rendered = str(context)
    assert context["known_slots"]["waybill_no"] == "JT123456781"
    assert context["last_tool_result"]["status"] == "in_transit"
    assert "84912345678" not in rendered
    assert "Sensitive address" not in rendered


def test_plain_text_reply_removes_markdown_markers() -> None:
    assert CustomerServiceAgent._plain_text_reply(
        "已创建工单 **CMP1234567890**，请保留 `ticket_id`。"
    ) == "已创建工单 CMP1234567890，请保留 ticket_id。"


def test_only_identifiers_in_safe_draft_are_required_in_rewrite() -> None:
    data = {"ticket_id": "CMP1234567890", "waybill_no": "JT123456781"}
    assert CustomerServiceAgent._required_identifiers(
        data, "工单 CMP1234567890 当前正在处理中。"
    ) == {"CMP1234567890"}


async def test_model_connection_failure_opens_short_cooldown(container) -> None:
    class FailingDeepAgent:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, payload):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise ConnectionError("offline")

    failing = FailingDeepAgent()
    container.agent.deep_agent = failing
    container.agent.settings.model_failure_cooldown_seconds = 30
    context = RequestContext(
        session_id="cooldown",
        user_id="u1",
        request_id="r1",
        trace_id="t1",
    )

    first = await container.agent._route_with_deep_agent(
        "ambiguous request", ConversationState(), context
    )
    second = await container.agent._route_with_deep_agent(
        "another ambiguous request", ConversationState(), context
    )

    assert first is None
    assert second is None
    assert failing.calls == 1
