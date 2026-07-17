from customer_service_agent.schemas import ChatRequest, SceneStatus


async def test_new_waybill_clears_phone_verification(container) -> None:
    session = "modify-waybill"
    await container.agent.ainvoke(
        ChatRequest(session_id=session, user_id="u1", message="显示签收了但没收到")
    )
    await container.agent.ainvoke(
        ChatRequest(session_id=session, user_id="u1", message="JT123456785")
    )
    pending = await container.agent.ainvoke(
        ChatRequest(session_id=session, user_id="u1", message="1234")
    )
    assert pending.status == SceneStatus.WAITING_CONFIRMATION

    changed = await container.agent.ainvoke(
        ChatRequest(session_id=session, user_id="u1", message="刚才错了，改成 JT123456786")
    )
    assert changed.status == SceneStatus.COLLECTING
    assert changed.action_required == "provide_phone_last4"
    state = await container.checkpointer.get(session)
    assert state.slots["waybill_no"] == "JT123456786"
    assert state.slots["phone_last4"] is None
    assert state.pending_confirmation is None


async def test_phone_can_be_modified_before_confirmation(container) -> None:
    session = "modify-phone"
    await container.agent.ainvoke(
        ChatRequest(
            session_id=session,
            user_id="u1",
            message="显示签收了但没收到 JT123456785",
        )
    )
    await container.agent.ainvoke(ChatRequest(session_id=session, user_id="u1", message="1234"))
    changed = await container.agent.ainvoke(
        ChatRequest(session_id=session, user_id="u1", message="手机号后四位改成 5678")
    )
    assert changed.status == SceneStatus.FAILED
    assert changed.current_step == "identity_failed"
