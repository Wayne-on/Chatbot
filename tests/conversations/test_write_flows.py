from customer_service_agent.schemas import ChatRequest, Intent, SceneStatus


async def test_delivery_followup_confirmation(container, backend) -> None:
    first = await container.agent.ainvoke(
        ChatRequest(session_id="urge-1", user_id="u1", message="物流一直没更新，催件 JT123456787")
    )
    assert first.status == SceneStatus.WAITING_CONFIRMATION
    assert not backend.audit_records
    second = await container.agent.ainvoke(
        ChatRequest(session_id="urge-1", user_id="u1", message="确认")
    )
    assert second.status == SceneStatus.COMPLETED
    assert second.data["ticket_id"].startswith("URG")
    saved = await container.checkpointer.get("urge-1")
    assert saved.slots["ticket_id"] == second.data["ticket_id"]


async def test_address_change_confirmation(container, backend) -> None:
    first = await container.agent.ainvoke(
        ChatRequest(session_id="address-1", user_id="u1", message="修改地址 JT123456781")
    )
    assert first.action_required == "provide_new_address"
    second = await container.agent.ainvoke(
        ChatRequest(
            session_id="address-1",
            user_id="u1",
            message="新地址是 123 Nguyen Hue Street, District 1",
        )
    )
    assert second.status == SceneStatus.WAITING_CONFIRMATION
    assert not backend.audit_records
    third = await container.agent.ainvoke(
        ChatRequest(session_id="address-1", user_id="u1", message="确认")
    )
    assert third.status == SceneStatus.COMPLETED
    assert third.data["request_id"].startswith("ADR")


async def test_created_complaint_ticket_is_reused_for_status_followup(container) -> None:
    first = await container.agent.ainvoke(
        ChatRequest(
            session_id="complaint-followup",
            user_id="u1",
            message="投诉运单 JT123456787，物流一直不更新",
            language="zh-CN",
        )
    )
    assert "general_complaint" not in first.reply
    created = await container.agent.ainvoke(
        ChatRequest(
            session_id="complaint-followup",
            user_id="u1",
            message="确认",
            language="zh-CN",
        )
    )
    queried = await container.agent.ainvoke(
        ChatRequest(
            session_id="complaint-followup",
            user_id="u1",
            message="这个工单多久能处理？",
            language="zh-CN",
        )
    )

    assert queried.current_intent == Intent.QUERY_COMPLAINT
    assert queried.data["ticket_id"] == created.data["ticket_id"]
