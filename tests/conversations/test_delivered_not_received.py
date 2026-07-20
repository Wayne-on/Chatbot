from customer_service_agent.schemas import ChatRequest, Intent, SceneStatus


async def test_delivered_not_received_requires_identity_and_confirmation(
    container, backend
) -> None:
    first = await container.agent.ainvoke(
        ChatRequest(session_id="dnr-1", user_id="u1", message="显示签收了但没收到")
    )
    assert first.current_intent == Intent.DELIVERED_NOT_RECEIVED
    assert first.action_required == "provide_waybill_no"

    second = await container.agent.ainvoke(
        ChatRequest(session_id="dnr-1", user_id="u1", message="JT123456785")
    )
    assert second.status == SceneStatus.COLLECTING
    assert second.action_required == "provide_phone_last4"
    assert "JT123456785" in second.reply
    assert "门卫/前台" in second.reply

    third = await container.agent.ainvoke(
        ChatRequest(session_id="dnr-1", user_id="u1", message="1234")
    )
    assert third.status == SceneStatus.WAITING_CONFIRMATION
    assert third.action_required == "confirm_action"
    assert not backend.audit_records

    fourth = await container.agent.ainvoke(
        ChatRequest(session_id="dnr-1", user_id="u1", message="确认")
    )
    assert fourth.status == SceneStatus.COMPLETED
    assert fourth.data["ticket_id"].startswith("CMP")
    assert len(backend.audit_records) == 1
    saved = await container.checkpointer.get("dnr-1")
    assert saved.slots["ticket_id"] == fourth.data["ticket_id"]


async def test_non_delivered_waybill_does_not_create_complaint(container, backend) -> None:
    await container.agent.ainvoke(
        ChatRequest(session_id="dnr-2", user_id="u1", message="签收了但未收到")
    )
    result = await container.agent.ainvoke(
        ChatRequest(session_id="dnr-2", user_id="u1", message="JT123456781")
    )
    assert result.status == SceneStatus.COMPLETED
    assert result.data["status"] == "in_transit"
    assert not backend.audit_records
