from customer_service_agent.schemas import ChatRequest, Intent, SceneStatus


async def send(container, session_id: str, message: str):  # type: ignore[no-untyped-def]
    return await container.agent.ainvoke(
        ChatRequest(
            session_id=session_id,
            user_id="u1",
            message=message,
            language="zh-CN",
        )
    )


async def test_user_reported_dialogue_remains_contextual_without_model(container) -> None:
    session_id = "reported-context-sequence"

    first = await send(container, session_id, "帮我查一下快递")
    assert first.action_required == "provide_waybill_no"

    tracked = await send(container, session_id, "JT123456781")
    assert tracked.status == SceneStatus.COMPLETED

    slow = await send(container, session_id, "怎么这么慢")
    assert slow.status == SceneStatus.WAITING_CONFIRMATION
    assert slow.action_required == "confirm_action"
    assert "JT123456781" in slow.reply

    hesitation = await send(container, session_id, "额")
    assert hesitation.status == SceneStatus.WAITING_CONFIRMATION
    assert "还没有提交" in hesitation.reply

    urged = await send(container, session_id, "能快些吗")
    assert urged.status == SceneStatus.COMPLETED
    assert urged.data["ticket_id"].startswith("URG")

    reused = await send(container, session_id, "帮我查一下快递")
    assert reused.status == SceneStatus.COMPLETED
    assert reused.action_required is None
    assert reused.data["waybill_no"] == "JT123456781"

    invalid = await send(container, session_id, "JT12344112231211")
    assert invalid.status == SceneStatus.COLLECTING
    assert invalid.action_required == "provide_waybill_no"
    assert "不符合" in invalid.reply

    second_tracking = await send(container, session_id, "JT1234567890123")
    assert second_tracking.status == SceneStatus.COMPLETED
    assert second_tracking.data["waybill_no"] == "JT1234567890123"

    praise = await send(container, session_id, "优秀")
    assert praise.current_intent == Intent.CONVERSATION
    assert "谢谢" in praise.reply
    assert "JT1234567890123" in praise.reply

    clearer_praise = await send(container, session_id, "我说你很棒")
    assert clearer_praise.current_intent == Intent.CONVERSATION
    assert "谢谢" in clearer_praise.reply

    memory = await send(container, session_id, "你还记得我查了几个单子吗")
    assert memory.current_intent == Intent.CONVERSATION
    assert memory.status == SceneStatus.COMPLETED
    assert "3 个" in memory.reply
    assert "JT123456781" in memory.reply
    assert "JT12344112231211" in memory.reply
    assert "JT1234567890123" in memory.reply
    assert memory.action_required is None


async def test_social_reply_does_not_cancel_unfinished_slot_collection(container) -> None:
    session_id = "social-during-slot"
    started = await send(container, session_id, "帮我查一下快递")
    assert started.current_step == "waiting_waybill"

    thanked = await send(container, session_id, "谢谢")
    assert thanked.current_intent == Intent.TRACKING
    assert thanked.current_step == "waiting_waybill"
    assert thanked.action_required == "provide_waybill_no"
    assert "谢谢" in thanked.reply

    completed = await send(container, session_id, "JT123456781")
    assert completed.status == SceneStatus.COMPLETED
    assert completed.data["waybill_no"] == "JT123456781"
