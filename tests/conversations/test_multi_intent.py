from customer_service_agent.schemas import (
    ChatRequest,
    Intent,
    IntentRelation,
    RouteDecision,
    SceneStatus,
)


async def test_read_only_multi_intents_complete_sequentially(container, backend) -> None:
    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        return RouteDecision(
            intent=Intent.TRACKING,
            secondary_intents=[Intent.PACKAGE_VOLUME],
            intent_relation=IntentRelation.PARALLEL,
            language="zh",
            explicit_intent=True,
        )

    container.agent.service.model_router = model_router
    response = await container.agent.ainvoke(
        ChatRequest(
            session_id="multi-read",
            user_id="u1",
            message="帮我查快递和包裹体积 JT123456781",
            language="zh-CN",
        )
    )

    assert response.status == SceneStatus.COMPLETED
    assert response.current_intent == Intent.PACKAGE_VOLUME
    assert [item["intent"] for item in response.data["results"]] == [
        "tracking",
        "query_package_volume",
    ]
    assert response.data["results"][0]["data"]["waybill_no"] == "JT123456781"
    assert response.data["results"][1]["data"]["waybill_no"] == "JT123456781"
    assert not backend.audit_records


async def test_shared_waybill_advances_from_tracking_to_address_collection(container) -> None:
    calls: list[str] = []

    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        calls.append(message)
        return RouteDecision(
            intent=Intent.TRACKING,
            secondary_intents=[Intent.CHANGE_ADDRESS],
            intent_relation=IntentRelation.AFTER,
            language="zh",
            explicit_intent=True,
        )

    container.agent.service.model_router = model_router
    first = await container.agent.ainvoke(
        ChatRequest(
            session_id="multi-address",
            user_id="u1",
            message="我要查快递和改地址",
            language="zh-CN",
        )
    )
    assert first.current_intent == Intent.TRACKING
    assert first.action_required == "provide_waybill_no"
    assert first.data["planned_intents"] == ["tracking", "change_address"]

    second = await container.agent.ainvoke(
        ChatRequest(
            session_id="multi-address",
            user_id="u1",
            message="JT123456781",
            language="zh-CN",
        )
    )
    assert second.current_intent == Intent.CHANGE_ADDRESS
    assert second.status == SceneStatus.COLLECTING
    assert second.action_required == "provide_new_address"
    assert [item["intent"] for item in second.data["results"]] == [
        "tracking",
        "change_address",
    ]
    assert "JT123456781" in second.reply
    assert calls == ["我要查快递和改地址"]
    saved = await container.checkpointer.get("multi-address")
    assert saved.pending_intents == []
    assert saved.slots["waybill_no"] == "JT123456781"


async def test_two_write_goals_require_separate_confirmations(container, backend) -> None:
    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        return RouteDecision(
            intent=Intent.COMPLAINT,
            secondary_intents=[Intent.CHANGE_ADDRESS],
            intent_relation=IntentRelation.AFTER,
            language="zh",
            explicit_intent=True,
        )

    container.agent.service.model_router = model_router
    first = await container.agent.ainvoke(
        ChatRequest(
            session_id="multi-write",
            user_id="u1",
            message="我要投诉服务并修改地址 JT123456781",
            language="zh-CN",
        )
    )
    assert first.status == SceneStatus.WAITING_CONFIRMATION
    assert first.current_intent == Intent.COMPLAINT
    assert not backend.audit_records

    confirmed_complaint = await container.agent.ainvoke(
        ChatRequest(
            session_id="multi-write",
            user_id="u1",
            message="确认",
            language="zh-CN",
        )
    )
    assert confirmed_complaint.current_intent == Intent.CHANGE_ADDRESS
    assert confirmed_complaint.action_required == "provide_new_address"
    assert [record.action for record in backend.audit_records] == ["create_complaint"]

    await container.agent.ainvoke(
        ChatRequest(
            session_id="multi-write",
            user_id="u1",
            message="新地址是 123 Nguyen Hue Street, District 1",
            language="zh-CN",
        )
    )
    confirmed_address = await container.agent.ainvoke(
        ChatRequest(
            session_id="multi-write",
            user_id="u1",
            message="确认",
            language="zh-CN",
        )
    )
    assert confirmed_address.status == SceneStatus.COMPLETED
    assert [record.action for record in backend.audit_records] == [
        "create_complaint",
        "change_address",
    ]


async def test_model_resolves_negated_intent_instead_of_keyword_priority(container) -> None:
    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        return RouteDecision(
            intent=Intent.TRACKING,
            language="zh",
            explicit_intent=True,
        )

    container.agent.service.model_router = model_router
    response = await container.agent.ainvoke(
        ChatRequest(
            session_id="negated-intent",
            user_id="u1",
            message="我不是要投诉，只想查快递 JT123456781",
            language="zh-CN",
        )
    )

    assert response.current_intent == Intent.TRACKING
    assert response.status == SceneStatus.COMPLETED
    saved = await container.checkpointer.get("negated-intent")
    assert saved.pending_intents == []


async def test_semantic_conflict_degrades_to_clarification_without_model(container) -> None:
    response = await container.agent.ainvoke(
        ChatRequest(
            session_id="negated-offline",
            user_id="u1",
            message="我不是要投诉，只想查快递 JT123456781",
            language="zh-CN",
        )
    )

    assert response.current_intent == Intent.FALLBACK
    assert response.action_required == "clarify_intent"
    assert "否定或更正" in response.reply


async def test_alternative_intents_ask_user_to_choose(container) -> None:
    response = await container.agent.ainvoke(
        ChatRequest(
            session_id="alternative-intents",
            user_id="u1",
            message="我想查快递或者修改地址",
            language="zh-CN",
        )
    )

    assert response.current_intent == Intent.FALLBACK
    assert response.action_required == "choose_intent"
    assert response.data["intent_choices"] == ["tracking", "change_address"]


async def test_cancelling_first_write_clears_remaining_goals(container, backend) -> None:
    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        return RouteDecision(
            intent=Intent.COMPLAINT,
            secondary_intents=[Intent.CHANGE_ADDRESS],
            intent_relation=IntentRelation.AFTER,
            language="zh",
            explicit_intent=True,
        )

    container.agent.service.model_router = model_router
    await container.agent.ainvoke(
        ChatRequest(
            session_id="cancel-multi-write",
            user_id="u1",
            message="我要投诉并修改地址 JT123456781",
            language="zh-CN",
        )
    )
    cancelled = await container.agent.ainvoke(
        ChatRequest(
            session_id="cancel-multi-write",
            user_id="u1",
            message="取消",
            language="zh-CN",
        )
    )

    assert cancelled.status == SceneStatus.CANCELLED
    saved = await container.checkpointer.get("cancel-multi-write")
    assert saved.pending_intents == []
    assert not backend.audit_records
