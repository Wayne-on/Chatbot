from customer_service_agent.schemas import ChatRequest, Intent, RouteDecision, SceneStatus


async def test_deterministic_route_skips_semantic_langgraph_node(container) -> None:
    awaiting_waybill = await container.agent.ainvoke(
        ChatRequest(
            session_id="graph-fast-path",
            user_id="u1",
            message="查快递",
            language="zh-CN",
        )
    )
    assert awaiting_waybill.action_required == "provide_waybill_no"

    calls: list[str] = []

    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        calls.append(message)
        return RouteDecision(intent=Intent.FAQ, language="en", explicit_intent=True)

    container.agent.service.model_router = model_router
    response = await container.agent.ainvoke(
        ChatRequest(
            session_id="graph-fast-path",
            user_id="u1",
            message="JT123456781",
            language="zh-CN",
        )
    )

    assert response.current_intent == Intent.TRACKING
    assert response.status == SceneStatus.COMPLETED
    assert calls == []


async def test_ambiguous_new_scene_can_use_structured_model_router(container) -> None:
    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        return RouteDecision(intent=Intent.FAQ, language="en", explicit_intent=True)

    container.agent.service.model_router = model_router
    response = await container.agent.ainvoke(
        ChatRequest(session_id="model-route-1", user_id="u1", message="ambiguous policy wording")
    )
    assert response.current_intent == Intent.FAQ
    assert response.status == SceneStatus.COMPLETED


async def test_new_scene_mode_uses_model_for_ambiguous_turn_then_continues_deterministically(
    container,
) -> None:
    calls: list[str] = []

    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        calls.append(message)
        return RouteDecision(intent=Intent.TRACKING, language="zh", explicit_intent=True)

    container.settings.model_routing_mode = "new_scene"
    container.agent.service.model_router = model_router
    first = await container.agent.ainvoke(
        ChatRequest(
            session_id="new-scene-route",
            user_id="u1",
            message="能帮我看看现在的情况吗",
        )
    )
    assert first.status == SceneStatus.COLLECTING
    assert first.action_required == "provide_waybill_no"

    second = await container.agent.ainvoke(
        ChatRequest(session_id="new-scene-route", user_id="u1", message="JT123456781")
    )
    assert second.status == SceneStatus.COMPLETED
    assert calls == ["能帮我看看现在的情况吗"]


async def test_model_routed_related_scene_inherits_existing_waybill(container) -> None:
    session_id = "model-context-reuse"
    tracked = await container.agent.ainvoke(
        ChatRequest(
            session_id=session_id,
            user_id="u1",
            message="查快递 JT123456781",
            language="zh-CN",
        )
    )
    assert tracked.status == SceneStatus.COMPLETED

    calls: list[str] = []

    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        calls.append(message)
        return RouteDecision(
            intent=Intent.DELIVERY_FOLLOWUP,
            language="zh",
            explicit_intent=True,
        )

    container.settings.model_routing_mode = "new_scene"
    container.agent.service.model_router = model_router
    response = await container.agent.ainvoke(
        ChatRequest(
            session_id=session_id,
            user_id="u1",
            message="它是不是卡在路上了？",
            language="zh-CN",
        )
    )
    assert response.current_intent == Intent.DELIVERY_FOLLOWUP
    assert response.action_required != "provide_waybill_no"
    assert response.data["waybill_no"] == "JT123456781"
    assert calls == ["它是不是卡在路上了？"]

    short_followup = await container.agent.ainvoke(
        ChatRequest(
            session_id=session_id,
            user_id="u1",
            message="？",
            language="zh-CN",
        )
    )
    assert short_followup.action_required != "provide_waybill_no"
    assert short_followup.data["waybill_no"] == "JT123456781"
    # Like the source DSL, a semantic follow-up is planned with real recent history.
    assert calls == ["它是不是卡在路上了？"]


async def test_model_router_receives_recent_user_and_assistant_messages(container) -> None:
    await container.agent.ainvoke(
        ChatRequest(
            session_id="model-history",
            user_id="u1",
            message="查快递 JT123456781",
            language="zh-CN",
        )
    )
    observed: list[tuple[str, str]] = []

    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        observed.extend((item.role, item.content) for item in state.recent_messages)
        return RouteDecision(
            intent=Intent.DELIVERY_FOLLOWUP,
            language="zh",
            continuation=True,
        )

    container.settings.model_routing_mode = "new_scene"
    container.agent.service.model_router = model_router
    response = await container.agent.ainvoke(
        ChatRequest(
            session_id="model-history",
            user_id="u1",
            message="它是不是卡在路上了？",
            language="zh-CN",
        )
    )

    assert response.current_intent == Intent.DELIVERY_FOLLOWUP
    assert observed[0] == ("user", "查快递 JT123456781")
    assert observed[1][0] == "assistant"
    assert "JT123456781" in observed[1][1]


async def test_regex_waybill_survives_when_model_omits_it(container) -> None:
    calls: list[str] = []

    async def model_router(message, state, context):  # type: ignore[no-untyped-def]
        calls.append(message)
        return RouteDecision(intent=Intent.TRACKING, language="zh", explicit_intent=True)

    container.settings.model_routing_mode = "new_scene"
    container.agent.service.model_router = model_router
    response = await container.agent.ainvoke(
        ChatRequest(
            session_id="model-slot-fallback",
            user_id="u1",
            message="麻烦看看 JT123456781 到哪了",
            language="zh-CN",
        )
    )

    assert response.status == SceneStatus.COMPLETED
    assert response.data["waybill_no"] == "JT123456781"
    assert calls == ["麻烦看看 JT123456781 到哪了"]
