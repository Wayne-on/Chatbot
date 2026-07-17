from customer_service_agent.router import (
    Router,
    extract_invalid_waybill,
    extract_waybill,
    normalize_waybill,
)
from customer_service_agent.schemas import Intent, IntentRelation, SceneStatus
from customer_service_agent.state import ConversationState


def test_waybill_parsing_and_validation() -> None:
    assert extract_waybill("请查 JT123456789") == "JT123456789"
    assert extract_waybill("waybill 12345678") == "12345678"
    assert normalize_waybill("JT-123456789") == "JT123456789"
    assert normalize_waybill("JT12") is None


def test_extract_invalid_waybill_candidate_without_accepting_valid_prefix() -> None:
    assert extract_invalid_waybill("JT12344112231211") == "JT12344112231211"
    assert extract_invalid_waybill("JT1234567890123") is None


def test_social_and_short_delivery_followup_routing() -> None:
    router = Router()
    state = ConversationState(
        current_intent=Intent.TRACKING,
        scene_status=SceneStatus.COMPLETED,
        language="zh",
    )
    state.remember_waybill("JT123456781")

    assert router.route("优秀", requested_language=None, state=state).intent == Intent.CONVERSATION
    assert router.route("额", requested_language=None, state=state).intent == Intent.CONVERSATION
    assert (
        router.route("能快些吗", requested_language=None, state=state).intent
        == Intent.DELIVERY_FOLLOWUP
    )


def test_waybill_only_continues_active_scene() -> None:
    state = ConversationState(
        current_intent=Intent.DELIVERED_NOT_RECEIVED,
        current_step="waiting_waybill",
        scene_status=SceneStatus.COLLECTING,
        language="zh",
    )
    decision = Router().route("JT123456785", requested_language=None, state=state)
    assert decision.intent == Intent.DELIVERED_NOT_RECEIVED
    assert decision.waybill_no == "JT123456785"
    assert decision.language == "zh"


def test_switch_and_cancel_are_both_detected() -> None:
    state = ConversationState(
        current_intent=Intent.DELIVERED_NOT_RECEIVED,
        current_step="waiting_phone_last4",
        scene_status=SceneStatus.COLLECTING,
    )
    decision = Router().route("算了，我想查另一个快递", requested_language=None, state=state)
    assert decision.cancel_requested is True
    assert decision.intent == Intent.TRACKING
    assert decision.explicit_intent is True


def test_multi_intents_are_kept_in_user_mention_order() -> None:
    decision = Router().route(
        "我要查快递和改地址",
        requested_language="zh-CN",
        state=ConversationState(),
    )

    assert decision.intent == Intent.TRACKING
    assert decision.secondary_intents == [Intent.CHANGE_ADDRESS]
    assert decision.intent_relation == IntentRelation.PARALLEL


def test_negated_multi_intent_requires_semantic_resolution() -> None:
    decision = Router().route(
        "我不是要投诉，只想查快递",
        requested_language="zh-CN",
        state=ConversationState(),
    )

    assert decision.intent == Intent.COMPLAINT
    assert decision.secondary_intents == [Intent.TRACKING]
    assert decision.intent_relation == IntentRelation.CORRECTION
    assert decision.semantic_conflict is True
