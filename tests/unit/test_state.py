import asyncio

from customer_service_agent.schemas import Intent, IntentRelation, PendingIntent, SceneStatus
from customer_service_agent.state import ConversationState, InMemoryConversationCheckpointer


def test_reset_clears_sensitive_scene_slots() -> None:
    state = ConversationState(
        current_intent=Intent.CHANGE_ADDRESS,
        scene_status=SceneStatus.WAITING_CONFIRMATION,
    )
    state.slots["new_address"] = "123 Example Street, District 1"
    state.slots["phone_last4"] = "1234"
    state.pending_intents = [
        PendingIntent(
            intent=Intent.TRACKING,
            relation=IntentRelation.AFTER,
            source_message="then track it",
        )
    ]
    state.reset_scene(status=SceneStatus.CANCELLED)
    assert state.current_intent is None
    assert state.slots["new_address"] is None
    assert state.slots["phone_last4"] is None
    assert state.scene_status == SceneStatus.CANCELLED
    assert state.pending_intents == []


def test_scene_transition_can_preserve_pending_intent_queue() -> None:
    state = ConversationState(current_intent=Intent.TRACKING)
    state.pending_intents = [
        PendingIntent(
            intent=Intent.CHANGE_ADDRESS,
            relation=IntentRelation.AFTER,
            source_message="track it and change the address",
        )
    ]

    state.reset_scene(preserve_pending_intents=True)

    assert [item.intent for item in state.pending_intents] == [Intent.CHANGE_ADDRESS]


def test_recent_history_keeps_latest_six_exchanges_across_scene_reset() -> None:
    state = ConversationState()
    state.last_tool_result = {"status": "in_transit"}
    state.remember_waybill("JT123456781")
    state.remember_ticket("CMP1234567890")
    for index in range(8):
        state.append_turn(f"user-{index}", f"assistant-{index}")
    state.reset_scene(status=SceneStatus.CANCELLED)

    assert len(state.recent_messages) == 12
    assert state.recent_messages[0].content == "user-2"
    assert state.recent_messages[-1].content == "assistant-7"
    assert state.last_tool_result == {"status": "in_transit"}
    assert state.last_waybill_no == "JT123456781"
    assert state.last_ticket_id == "CMP1234567890"


def test_new_owner_clear_removes_history_and_scene_data() -> None:
    state = ConversationState(current_intent=Intent.TRACKING)
    state.slots["waybill_no"] = "JT123456781"
    state.last_tool_result = {"status": "delivered"}
    state.remember_waybill("JT123456781")
    state.remember_ticket("CMP1234567890")
    state.append_turn("查快递", "请提供运单号")
    state.clear_for_new_owner()

    assert state.current_intent is None
    assert state.slots["waybill_no"] is None
    assert state.recent_messages == []
    assert state.last_tool_result is None
    assert state.waybill_history == []
    assert state.valid_waybill_history == []
    assert state.ticket_history == []


def test_waybill_history_keeps_invalid_attempts_but_reuses_only_valid_values() -> None:
    state = ConversationState()
    state.remember_waybill("JT123456781")
    state.remember_waybill("JT12344112231211", valid=False)
    state.remember_waybill("JT1234567890123")
    state.remember_waybill("JT123456781")

    assert state.waybill_history == [
        "JT123456781",
        "JT12344112231211",
        "JT1234567890123",
    ]
    assert state.valid_waybill_history == ["JT123456781", "JT1234567890123"]
    assert state.last_waybill_no == "JT1234567890123"


async def test_checkpointer_isolates_threads_and_returns_copies() -> None:
    checkpointer = InMemoryConversationCheckpointer()
    async with checkpointer.session("a") as state:
        state.slots["waybill_no"] = "JT123456785"
    state_a = await checkpointer.get("a")
    state_b = await checkpointer.get("b")
    assert state_a.slots["waybill_no"] == "JT123456785"
    assert state_b.slots["waybill_no"] is None
    state_a.slots["waybill_no"] = "MUTATED"
    assert (await checkpointer.get("a")).slots["waybill_no"] == "JT123456785"


async def test_same_thread_updates_are_serialized() -> None:
    checkpointer = InMemoryConversationCheckpointer()

    async def update(value: str) -> None:
        async with checkpointer.session("thread") as state:
            await asyncio.sleep(0.001)
            state.scene_context[value] = True

    await asyncio.gather(update("one"), update("two"))
    state = await checkpointer.get("thread")
    assert state.scene_context == {"one": True, "two": True}
