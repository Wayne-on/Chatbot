from customer_service_agent.schemas import ChatRequest, Intent, SceneStatus


async def test_scene_switch_clears_previous_slots(container) -> None:
    await container.agent.ainvoke(
        ChatRequest(session_id="switch-1", user_id="u1", message="签收了但没收到")
    )
    await container.agent.ainvoke(
        ChatRequest(session_id="switch-1", user_id="u1", message="JT123456785")
    )
    result = await container.agent.ainvoke(
        ChatRequest(session_id="switch-1", user_id="u1", message="算了，我想查另一个快递")
    )
    assert result.current_intent == Intent.TRACKING
    assert result.current_step == "waiting_waybill"
    state = await container.checkpointer.get("switch-1")
    assert state.slots["phone_last4"] is None
    assert state.pending_confirmation is None


async def test_cancel_pending_write_makes_no_mutation(container, backend) -> None:
    await container.agent.ainvoke(
        ChatRequest(
            session_id="cancel-1",
            user_id="u1",
            message="投诉运单 JT123456781，物流太慢了",
        )
    )
    result = await container.agent.ainvoke(
        ChatRequest(session_id="cancel-1", user_id="u1", message="取消")
    )
    assert result.status == SceneStatus.CANCELLED
    assert not backend.audit_records


async def test_tool_failure_does_not_invent_tracking(container, backend) -> None:
    backend.fail_next("query_tracking")
    result = await container.agent.ainvoke(
        ChatRequest(session_id="fail-1", user_id="u1", message="查快递 JT123456781")
    )
    assert result.status == SceneStatus.FAILED
    assert result.data == {}
    assert "in_transit" not in result.reply
    assert "BUSINESS_API_TIMEOUT" in result.reply
