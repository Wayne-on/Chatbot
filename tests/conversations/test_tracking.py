from customer_service_agent.schemas import ChatRequest, Intent, SceneStatus


async def test_tracking_multiturn(container) -> None:
    first = await container.agent.ainvoke(
        ChatRequest(session_id="tracking-1", user_id="u1", message="帮我查一下快递")
    )
    assert first.status == SceneStatus.COLLECTING
    assert first.current_intent == Intent.TRACKING
    assert first.current_step == "waiting_waybill"

    second = await container.agent.ainvoke(
        ChatRequest(session_id="tracking-1", user_id="u1", message="JT123456781")
    )
    assert second.status == SceneStatus.COMPLETED
    assert second.data["status"] == "in_transit"
    assert "JT123456781" in second.reply
    assert "运输中" in second.reply
    assert "in_transit" not in second.reply


async def test_completed_tracking_followups_reuse_known_waybill(container) -> None:
    session_id = "tracking-context"
    first = await container.agent.ainvoke(
        ChatRequest(
            session_id=session_id,
            user_id="u1",
            message="帮我查快递 JT123456781",
            language="zh-CN",
        )
    )
    assert first.status == SceneStatus.COMPLETED

    slow = await container.agent.ainvoke(
        ChatRequest(
            session_id=session_id,
            user_id="u1",
            message="怎么这么慢啊",
            language="zh-CN",
        )
    )
    assert slow.current_intent == Intent.DELIVERY_FOLLOWUP
    assert slow.status == SceneStatus.WAITING_CONFIRMATION
    assert slow.action_required != "provide_waybill_no"
    assert "JT123456781" in slow.reply

    question = await container.agent.ainvoke(
        ChatRequest(
            session_id=session_id,
            user_id="u1",
            message="？",
            language="zh-CN",
        )
    )
    assert question.current_intent == Intent.DELIVERY_FOLLOWUP
    assert question.action_required != "provide_waybill_no"
    assert question.data["waybill_no"] == "JT123456781"


async def test_completed_result_can_be_naturally_rewritten_with_history(container) -> None:
    observed_history_lengths: list[int] = []

    async def response_generator(message, state, response, context):  # type: ignore[no-untyped-def]
        observed_history_lengths.append(len(state.recent_messages))
        return f"我理解你是在问“{message}”。运单 {response.data['waybill_no']} 仍在运输中。"

    container.agent.service.response_generator = response_generator
    response = await container.agent.ainvoke(
        ChatRequest(
            session_id="natural-reply",
            user_id="u1",
            message="帮我查快递，看看这个件现在到底到哪了？JT123456781",
            language="zh-CN",
        )
    )

    assert response.reply.startswith("我理解你是在问")
    assert observed_history_lengths == [0]
    saved = await container.checkpointer.get("natural-reply")
    assert [(item.role, item.content) for item in saved.recent_messages] == [
        ("user", "帮我查快递，看看这个件现在到底到哪了？JT123456781"),
        ("assistant", response.reply),
    ]


async def test_new_user_cannot_read_reused_session_state(container) -> None:
    await container.agent.ainvoke(
        ChatRequest(session_id="shared", user_id="owner", message="查快递 JT123456781")
    )
    response = await container.agent.ainvoke(
        ChatRequest(session_id="shared", user_id="intruder", message="查快递")
    )
    assert response.status == SceneStatus.COLLECTING
    assert response.action_required == "provide_waybill_no"
    assert "JT123456781" not in response.reply
