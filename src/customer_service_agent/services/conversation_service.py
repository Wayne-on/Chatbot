from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any
from uuid import uuid4

from customer_service_agent.config import Settings
from customer_service_agent.middleware.security import hash_subject, mask_user_id
from customer_service_agent.router import Router
from customer_service_agent.schemas import (
    ChatRequest,
    ChatResponse,
    Intent,
    IntentRelation,
    PendingIntent,
    RequestContext,
    RouteDecision,
    SceneStatus,
    ToolResult,
)
from customer_service_agent.services.response_service import ResponseService
from customer_service_agent.services.scene_manager import SceneManager
from customer_service_agent.state import ConversationCheckpointer, ConversationState
from customer_service_agent.tools.complaint import CreateComplaintInput, QueryComplaintInput
from customer_service_agent.tools.delivery import (
    ChangeAddressInput,
    CheckAddressChangeInput,
    UrgeDeliveryInput,
)
from customer_service_agent.tools.identity import VerifyReceiverInput
from customer_service_agent.tools.knowledge import RetrieveFAQInput, TransferToHumanInput
from customer_service_agent.tools.service import BusinessTools
from customer_service_agent.tools.tracking import QueryPackageVolumeInput, QueryTrackingInput

logger = logging.getLogger(__name__)

WAYBILL_INTENTS = {
    Intent.TRACKING,
    Intent.PACKAGE_VOLUME,
    Intent.DELIVERY_FOLLOWUP,
    Intent.DELIVERED_NOT_RECEIVED,
    Intent.CHANGE_ADDRESS,
    Intent.COMPLAINT,
}


class ConversationService:
    def __init__(
        self,
        *,
        settings: Settings,
        checkpointer: ConversationCheckpointer,
        router: Router,
        tools: BusinessTools,
        responses: ResponseService,
        scenes: SceneManager,
    ) -> None:
        self.settings = settings
        self.checkpointer = checkpointer
        self.router = router
        self.tools = tools
        self.responses = responses
        self.scenes = scenes
        self.model_router: (
            Callable[[str, ConversationState, RequestContext], Awaitable[RouteDecision | None]]
            | None
        ) = None
        self.response_generator: (
            Callable[[str, ConversationState, ChatResponse, RequestContext], Awaitable[str | None]]
            | None
        ) = None

    async def handle(self, request: ChatRequest, *, trace_id: str | None = None) -> ChatResponse:
        """Compatibility entrypoint used outside the LangGraph API workflow."""
        started = perf_counter()
        trace_id = trace_id or uuid4().hex
        context = self.build_context(request, trace_id)
        async with self.checkpointer.session(request.session_id) as state:
            self.prepare_state(state, request.user_id)
            rule_decision = self.route_deterministically(request, state)
            model_decision: RouteDecision | None = None
            if self.should_use_model_router(state, rule_decision):
                assert self.model_router is not None
                model_decision = await self.model_router(request.message, state, context)
            decision = self.resolve_decision(rule_decision, model_decision)
            return await self.execute_decision(
                request,
                state,
                context,
                decision,
                started=started,
            )

    @staticmethod
    def build_context(request: ChatRequest, trace_id: str) -> RequestContext:
        return RequestContext(
            session_id=request.session_id,
            user_id=request.user_id,
            request_id=request.request_id or uuid4().hex,
            trace_id=trace_id,
            user_credential=request.user_credential,
        )

    @staticmethod
    def prepare_state(state: ConversationState, user_id: str) -> None:
        owner_hash = hash_subject(user_id)
        if state.owner_hash and state.owner_hash != owner_hash:
            # A reused session ID must never reveal another user's state.
            state.clear_for_new_owner()
        state.owner_hash = owner_hash

    def route_deterministically(
        self, request: ChatRequest, state: ConversationState
    ) -> RouteDecision:
        return self.router.route(
            request.message,
            requested_language=request.language,
            state=state,
        )

    def resolve_decision(
        self,
        rule_decision: RouteDecision,
        model_decision: RouteDecision | None,
    ) -> RouteDecision:
        decision = rule_decision
        if model_decision is not None and not (
            model_decision.intent == Intent.FALLBACK
            and rule_decision.intent is not None
            and not rule_decision.secondary_intents
            and not rule_decision.semantic_conflict
        ):
            return model_decision.model_copy(
                update={
                    "semantic_conflict": rule_decision.semantic_conflict,
                    # Deterministic confirmation/cancellation detection is authoritative.
                    "cancel_requested": (
                        model_decision.cancel_requested
                        if rule_decision.semantic_conflict
                        else rule_decision.cancel_requested or model_decision.cancel_requested
                    ),
                    "confirmation": rule_decision.confirmation,
                    "rejection": rule_decision.rejection,
                    "human_requested": (
                        model_decision.human_requested
                        if rule_decision.semantic_conflict
                        else rule_decision.human_requested or model_decision.human_requested
                    ),
                    # Regex-validated identifiers remain a deterministic fallback.
                    "waybill_no": model_decision.waybill_no or rule_decision.waybill_no,
                    "phone_last4": model_decision.phone_last4 or rule_decision.phone_last4,
                    "ticket_id": model_decision.ticket_id or rule_decision.ticket_id,
                    "new_address": model_decision.new_address or rule_decision.new_address,
                    "invalid_waybill_no": rule_decision.invalid_waybill_no,
                }
            )
        if rule_decision.semantic_conflict:
            mentioned = [
                intent
                for intent in [rule_decision.intent, *rule_decision.secondary_intents]
                if intent is not None
            ]
            decision = RouteDecision(
                intent=Intent.FALLBACK,
                language=rule_decision.language,
                clarify_question=self.responses.semantic_clarification(
                    rule_decision.language,
                    mentioned,
                ),
            )
        return decision

    def should_use_model_router(
        self, state: ConversationState, rule_decision: RouteDecision
    ) -> bool:
        if self.model_router is None:
            return False
        if rule_decision.invalid_waybill_no:
            return False
        if rule_decision.secondary_intents or rule_decision.semantic_conflict:
            # Natural-language multi-intent, negation, and correction need contextual semantics.
            return True
        if state.scene_status == SceneStatus.WAITING_CONFIRMATION:
            return False
        if rule_decision.cancel_requested or rule_decision.human_requested:
            return False
        if rule_decision.explicit_intent:
            # High-confidence keyword/phrase routes stay useful even during a model outage.
            return False
        # A value that exactly satisfies the requested slot should stay deterministic and cheap.
        deterministic_slots = {
            "waiting_waybill": bool(rule_decision.waybill_no),
            "waiting_phone_last4": bool(rule_decision.phone_last4),
            "waiting_new_address": bool(rule_decision.new_address),
            "waiting_ticket_id": bool(rule_decision.ticket_id),
            "waiting_complaint_description": bool(rule_decision.intent in {None, Intent.COMPLAINT}),
        }
        if state.active and deterministic_slots.get(state.current_step or "", False):
            return False

        return self.settings.model_routing_mode == "new_scene" or rule_decision.intent is None

    async def execute_decision(
        self,
        request: ChatRequest,
        state: ConversationState,
        context: RequestContext,
        decision: RouteDecision,
        *,
        started: float,
    ) -> ChatResponse:
        state.language = decision.language
        existing_waybill = state.slots.get("waybill_no") or state.last_waybill_no
        if (
            not state.active
            and not decision.waybill_no
            and not decision.invalid_waybill_no
            and existing_waybill
            and decision.intent in WAYBILL_INTENTS
        ):
            # A related follow-up reuses the known shipment unless the user supplies a new one.
            decision = decision.model_copy(update={"waybill_no": existing_waybill})
        existing_ticket = state.slots.get("ticket_id") or state.last_ticket_id
        if not decision.ticket_id and existing_ticket and decision.intent == Intent.QUERY_COMPLAINT:
            decision = decision.model_copy(update={"ticket_id": existing_ticket})
        if decision.waybill_no and existing_waybill and decision.waybill_no != existing_waybill:
            # Identity and address data are shipment-specific and must never carry over.
            state.slots["phone_last4"] = None
            state.slots["new_address"] = None
            state.scene_context = {}
        previous_intent = state.current_intent
        is_switch = bool(
            state.active
            and decision.explicit_intent
            and decision.intent
            and decision.intent != Intent.CONVERSATION
            and decision.intent != previous_intent
        )
        if is_switch:
            owner_hash = state.owner_hash
            state.reset_scene()
            state.owner_hash = owner_hash
            state.language = decision.language

        self._update_pending_intents(state, decision, request.message)

        if decision.intent_relation == IntentRelation.ALTERNATIVE and decision.secondary_intents:
            choices = [
                intent
                for intent in [decision.intent, *decision.secondary_intents]
                if intent is not None
            ]
            state.reset_scene()
            state.current_intent = Intent.FALLBACK
            state.language = decision.language
            self.scenes.collect(state, "waiting_intent")
            response = self._response(
                state,
                context.trace_id,
                self.responses.intent_choice(state.language, choices),
                action_required="choose_intent",
                data={"intent_choices": [intent.value for intent in choices]},
            )
        elif decision.cancel_requested and not is_switch:
            state.reset_scene(status=SceneStatus.CANCELLED)
            response = self._response(
                state, context.trace_id, self.responses.render(state.language, "cancelled")
            )
        elif decision.human_requested:
            response = await self._transfer(state, context, reason="user_requested")
        elif decision.invalid_waybill_no:
            response = self._invalid_waybill(
                state,
                context.trace_id,
                decision.invalid_waybill_no,
                decision.intent,
            )
        elif state.scene_status == SceneStatus.WAITING_CONFIRMATION and state.pending_confirmation:
            response = await self._handle_confirmation(state, decision, context, request.message)
        elif state.active and decision.intent == Intent.CONVERSATION:
            response = self._active_conversation(state, context.trace_id, request.message)
        else:
            response = await self._dispatch(state, decision, context, request.message)

        response = await self._advance_pending_intents(state, response, context)

        if (
            self.response_generator is not None
            and response.status == SceneStatus.COMPLETED
            and response.action_required is None
        ):
            generated_reply = await self.response_generator(
                request.message, state, response, context
            )
            if generated_reply:
                response.reply = generated_reply
        state.append_turn(request.message, response.reply)

        logger.info(
            "conversation trace_id=%s session_id=%s user_id=%s selected_skill=%s "
            "current_step=%s state_transition=%s total_latency_ms=%.2f",
            context.trace_id,
            request.session_id,
            mask_user_id(request.user_id),
            response.current_intent,
            response.current_step,
            response.status,
            (perf_counter() - started) * 1000,
        )
        return response

    @staticmethod
    def _update_pending_intents(
        state: ConversationState,
        decision: RouteDecision,
        message: str,
    ) -> None:
        if decision.intent_relation == IntentRelation.ALTERNATIVE:
            state.pending_intents = []
            return
        if decision.secondary_intents:
            seen = {decision.intent}
            state.pending_intents = []
            for intent in decision.secondary_intents:
                if intent in seen or intent in {Intent.CONVERSATION, Intent.FALLBACK}:
                    continue
                seen.add(intent)
                state.pending_intents.append(
                    PendingIntent(
                        intent=intent,
                        relation=decision.intent_relation,
                        source_message=message[:1000],
                        condition=decision.intent_condition,
                        phone_last4=(
                            decision.phone_last4
                            if intent == Intent.DELIVERED_NOT_RECEIVED
                            else None
                        ),
                        ticket_id=(
                            decision.ticket_id if intent == Intent.QUERY_COMPLAINT else None
                        ),
                        new_address=(
                            decision.new_address if intent == Intent.CHANGE_ADDRESS else None
                        ),
                    )
                )
            return
        if decision.semantic_conflict or decision.intent_relation == IntentRelation.CORRECTION:
            state.pending_intents = []

    async def _advance_pending_intents(
        self,
        state: ConversationState,
        response: ChatResponse,
        context: RequestContext,
    ) -> ChatResponse:
        """Advance recognized goals sequentially while retaining one authoritative active scene."""
        if response.status != SceneStatus.COMPLETED or response.action_required is not None:
            return response
        if not state.pending_intents:
            return response

        results = [
            {
                "intent": response.current_intent.value if response.current_intent else None,
                "data": response.data,
            }
        ]
        replies = [response.reply]
        next_response = response

        while (
            next_response.status == SceneStatus.COMPLETED
            and next_response.action_required is None
            and state.pending_intents
        ):
            pending = state.pending_intents.pop(0)
            decision = RouteDecision(
                intent=pending.intent,
                language=state.language,
                waybill_no=(state.last_waybill_no if pending.intent in WAYBILL_INTENTS else None),
                ticket_id=(
                    pending.ticket_id or state.last_ticket_id
                    if pending.intent == Intent.QUERY_COMPLAINT
                    else None
                ),
                phone_last4=pending.phone_last4,
                new_address=pending.new_address,
                explicit_intent=True,
                continuation=False,
                intent_relation=pending.relation,
                intent_condition=pending.condition,
                business_reason=pending.source_message,
            )
            next_response = await self._dispatch(
                state,
                decision,
                context,
                pending.source_message,
            )
            replies.append(next_response.reply)
            results.append(
                {
                    "intent": (
                        next_response.current_intent.value if next_response.current_intent else None
                    ),
                    "data": next_response.data,
                }
            )
            next_response = next_response.model_copy(
                update={
                    "reply": " ".join(replies),
                    "data": {
                        "results": results,
                        "pending_intents": [item.intent.value for item in state.pending_intents],
                    },
                }
            )

        return next_response

    async def _dispatch(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        intent = decision.intent or Intent.FALLBACK
        self.scenes.activate(state, intent, preserve_pending_intents=True)
        if decision.waybill_no:
            state.slots["waybill_no"] = decision.waybill_no
            state.remember_waybill(decision.waybill_no)
        if decision.phone_last4:
            state.slots["phone_last4"] = decision.phone_last4
        if decision.ticket_id:
            state.slots["ticket_id"] = decision.ticket_id
            state.remember_ticket(decision.ticket_id)
        if decision.new_address:
            state.slots["new_address"] = decision.new_address

        handlers = {
            Intent.TRACKING: self._tracking,
            Intent.PACKAGE_VOLUME: self._package_volume,
            Intent.DELIVERED_NOT_RECEIVED: self._delivered_not_received,
            Intent.DELIVERY_FOLLOWUP: self._delivery_followup,
            Intent.CHANGE_ADDRESS: self._change_address,
            Intent.COMPLAINT: self._complaint,
            Intent.QUERY_COMPLAINT: self._query_complaint,
            Intent.FAQ: self._faq,
            Intent.CONVERSATION: self._conversation,
            Intent.FALLBACK: self._fallback,
        }
        return await handlers[intent](state, decision, context, message)

    async def _tracking(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        waybill = state.slots.get("waybill_no")
        if not waybill:
            return self._ask_waybill(state, context.trace_id)
        result = await self.tools.query_tracking(QueryTrackingInput(waybill_no=waybill), context)
        if not result.succeeded:
            return await self._tool_failure(state, result, context)
        data = result.data
        state.last_tool_result = data
        self.scenes.complete(state)
        tracking_values = self.responses.tracking_values(state.language, data)
        reply = self.responses.render(
            state.language,
            "tracking_result",
            waybill=waybill,
            **tracking_values,
        )
        return self._response(state, context.trace_id, reply, data=self._safe_tracking(data))

    async def _package_volume(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        waybill = state.slots.get("waybill_no")
        if not waybill:
            return self._ask_waybill(state, context.trace_id)
        result = await self.tools.query_package_volume(
            QueryPackageVolumeInput(waybill_no=waybill), context
        )
        if not result.succeeded:
            return await self._tool_failure(state, result, context)
        data = result.data
        state.last_tool_result = data
        self.scenes.complete(state)
        reply = self.responses.render(
            state.language,
            "volume_result",
            waybill=waybill,
            length=data["length_cm"],
            width=data["width_cm"],
            height=data["height_cm"],
            volume=data["volume_cm3"],
            weight=data["volumetric_weight_kg"],
        )
        return self._response(state, context.trace_id, reply, data=data)

    async def _delivered_not_received(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        waybill = state.slots.get("waybill_no")
        if not waybill:
            return self._ask_waybill(state, context.trace_id)
        tracking = await self.tools.query_tracking(QueryTrackingInput(waybill_no=waybill), context)
        if not tracking.succeeded:
            return await self._tool_failure(state, tracking, context)
        data = tracking.data
        state.last_tool_result = data
        if data.get("status") != "delivered":
            self.scenes.complete(state)
            tracking_values = self.responses.tracking_values(state.language, data)
            reply = self.responses.render(
                state.language,
                "not_delivered",
                **tracking_values,
            )
            return self._response(state, context.trace_id, reply, data=self._safe_tracking(data))

        phone_last4 = state.slots.get("phone_last4")
        if not phone_last4:
            self.scenes.collect(state, "waiting_phone_last4")
            return self._response(
                state,
                context.trace_id,
                self.responses.render(state.language, "ask_phone_last4"),
                action_required="provide_phone_last4",
                data={"tracking": self._safe_tracking(data)},
            )
        verified = await self.tools.verify_receiver(
            VerifyReceiverInput(waybill_no=waybill, phone_last4=phone_last4), context
        )
        if not verified.succeeded:
            return await self._tool_failure(state, verified, context)
        if not verified.data.get("verified"):
            state.scene_status = SceneStatus.FAILED
            state.current_step = "identity_failed"
            return self._response(
                state,
                context.trace_id,
                self.responses.render(state.language, "identity_failed"),
                action_required="retry_phone_last4_or_contact_human",
            )
        self.scenes.set_pending(
            state,
            tool="create_complaint",
            arguments={
                "waybill_no": waybill,
                "complaint_type": "delivered_not_received",
                "description": "Tracking shows delivered but the verified receiver reports non-receipt.",
                "phone_last4": phone_last4,
            },
            prompt_key="confirm_delivered_complaint",
            context=context,
        )
        reply = self.responses.render(
            state.language, "confirm_delivered_complaint", waybill=waybill
        )
        return self._response(
            state,
            context.trace_id,
            reply,
            action_required="confirm_action",
            data={"tool": "create_complaint"},
        )

    async def _delivery_followup(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        waybill = state.slots.get("waybill_no")
        if not waybill:
            return self._ask_waybill(state, context.trace_id)
        tracking = await self.tools.query_tracking(QueryTrackingInput(waybill_no=waybill), context)
        if not tracking.succeeded:
            return await self._tool_failure(state, tracking, context)
        data = tracking.data
        state.last_tool_result = data
        tracking_values = self.responses.tracking_values(state.language, data)
        if not data.get("can_urge"):
            self.scenes.complete(state)
            reply = self.responses.render(
                state.language,
                "followup_unavailable",
                waybill=waybill,
                **tracking_values,
            )
            return self._response(state, context.trace_id, reply, data=self._safe_tracking(data))
        self.scenes.set_pending(
            state,
            tool="urge_delivery",
            arguments={"waybill_no": waybill, "reason": message[:500]},
            prompt_key="confirm_followup",
            context=context,
        )
        reply = self.responses.render(
            state.language,
            "confirm_followup",
            status=tracking_values["status"],
            waybill=waybill,
        )
        return self._response(
            state,
            context.trace_id,
            reply,
            action_required="confirm_action",
            data={
                "tool": "urge_delivery",
                "waybill_no": waybill,
                "tracking": self._safe_tracking(data),
            },
        )

    async def _change_address(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        waybill = state.slots.get("waybill_no")
        if not waybill:
            return self._ask_waybill(state, context.trace_id)
        check = await self.tools.check_address_change(
            CheckAddressChangeInput(waybill_no=waybill), context
        )
        if not check.succeeded:
            return await self._tool_failure(state, check, context)
        state.last_tool_result = check.data
        if not check.data.get("can_change"):
            self.scenes.complete(state)
            address_values = self.responses.address_unavailable_values(
                state.language,
                check.data,
            )
            reply = self.responses.render(
                state.language,
                "address_unavailable",
                **address_values,
            )
            return self._response(state, context.trace_id, reply, data=check.data)
        new_address = state.slots.get("new_address")
        if not new_address:
            self.scenes.collect(state, "waiting_new_address")
            return self._response(
                state,
                context.trace_id,
                self.responses.render(state.language, "ask_new_address"),
                action_required="provide_new_address",
            )
        self.scenes.set_pending(
            state,
            tool="change_address",
            arguments={"waybill_no": waybill, "new_address": new_address},
            prompt_key="confirm_address",
            context=context,
        )
        reply = self.responses.render(
            state.language,
            "confirm_address",
            waybill=waybill,
            address=new_address,
        )
        return self._response(
            state,
            context.trace_id,
            reply,
            action_required="confirm_action",
            data={"tool": "change_address"},
        )

    async def _complaint(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        if not state.slots.get("complaint_description") and decision.explicit_intent:
            state.slots["complaint_description"] = message[:1000]
            state.slots["complaint_type"] = self._complaint_type(message)
        waybill = state.slots.get("waybill_no")
        if not waybill:
            return self._ask_waybill(state, context.trace_id)
        description = state.slots.get("complaint_description")
        if not description:
            if len(message.strip()) >= 5 and state.current_step == "waiting_complaint_description":
                description = message.strip()[:1000]
                state.slots["complaint_description"] = description
                state.slots["complaint_type"] = self._complaint_type(message)
            else:
                self.scenes.collect(state, "waiting_complaint_description")
                return self._response(
                    state,
                    context.trace_id,
                    self.responses.render(state.language, "ask_complaint_description"),
                    action_required="provide_complaint_description",
                )
        complaint_type = state.slots.get("complaint_type") or "general_complaint"
        self.scenes.set_pending(
            state,
            tool="create_complaint",
            arguments={
                "waybill_no": waybill,
                "complaint_type": complaint_type,
                "description": description,
            },
            prompt_key="confirm_complaint",
            context=context,
        )
        reply = self.responses.render(
            state.language,
            "confirm_complaint",
            waybill=waybill,
            complaint_type=complaint_type,
        )
        return self._response(
            state,
            context.trace_id,
            reply,
            action_required="confirm_action",
            data={"tool": "create_complaint"},
        )

    async def _query_complaint(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        ticket_id = state.slots.get("ticket_id")
        if not ticket_id:
            self.scenes.collect(state, "waiting_ticket_id")
            return self._response(
                state,
                context.trace_id,
                self.responses.render(state.language, "ask_ticket_id"),
                action_required="provide_ticket_id",
            )
        result = await self.tools.query_complaint(QueryComplaintInput(ticket_id=ticket_id), context)
        if not result.succeeded:
            return await self._tool_failure(state, result, context)
        self.scenes.complete(state)
        if result.data.get("found") is False or result.data.get("status") == "not_found":
            reply = self.responses.render(state.language, "ticket_not_found", ticket_id=ticket_id)
        else:
            reply = self.responses.render(
                state.language,
                "ticket_status",
                ticket_id=ticket_id,
                status=result.data.get("status", "unknown"),
            )
        return self._response(state, context.trace_id, reply, data=result.data)

    async def _faq(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        result = await self.tools.retrieve_faq(
            RetrieveFAQInput(query=message, language=state.language), context
        )
        if not result.succeeded:
            return await self._tool_failure(state, result, context)
        state.last_tool_result = result.data
        self.scenes.complete(state)
        return self._response(state, context.trace_id, result.data["answer"], data=result.data)

    async def _conversation(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        self.scenes.complete(state)
        data = {
            "waybill_history": list(state.waybill_history),
            "valid_waybill_history": list(state.valid_waybill_history),
            "ticket_history": list(state.ticket_history),
            "last_waybill_no": state.last_waybill_no,
            "last_ticket_id": state.last_ticket_id,
        }
        reply = self.responses.conversation_reply(
            state.language,
            message,
            waybill_history=state.waybill_history,
            last_valid_waybill=state.last_waybill_no,
            last_ticket_id=state.last_ticket_id,
        )
        return self._response(state, context.trace_id, reply, data=data)

    def _active_conversation(
        self,
        state: ConversationState,
        trace_id: str,
        message: str,
    ) -> ChatResponse:
        """Respond socially without discarding an unfinished deterministic scene."""
        reply = self.responses.conversation_reply(
            state.language,
            message,
            waybill_history=state.waybill_history,
            last_valid_waybill=state.last_waybill_no,
            last_ticket_id=state.last_ticket_id,
        )
        reminders = {
            "waiting_waybill": ("ask_waybill", "provide_waybill_no"),
            "waiting_phone_last4": ("ask_phone_last4", "provide_phone_last4"),
            "waiting_new_address": ("ask_new_address", "provide_new_address"),
            "waiting_complaint_description": (
                "ask_complaint_description",
                "provide_complaint_description",
            ),
            "waiting_ticket_id": ("ask_ticket_id", "provide_ticket_id"),
            "waiting_intent": ("clarify", "clarify_intent"),
        }
        reminder = reminders.get(state.current_step or "")
        action_required = None
        if reminder:
            key, action_required = reminder
            reply = f"{reply} {self.responses.render(state.language, key)}"
        return self._response(
            state,
            trace_id,
            reply,
            action_required=action_required,
            data={
                "waybill_history": list(state.waybill_history),
                "last_waybill_no": state.last_waybill_no,
            },
        )

    async def _fallback(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        state.retry_count += 1
        if state.retry_count >= self.settings.max_scene_retries:
            return await self._transfer(state, context, reason="repeated_unresolved_intent")
        self.scenes.collect(state, "waiting_intent")
        reply = decision.clarify_question or self.responses.render(state.language, "clarify")
        return self._response(
            state,
            context.trace_id,
            reply,
            action_required="clarify_intent",
        )

    async def _handle_confirmation(
        self,
        state: ConversationState,
        decision: RouteDecision,
        context: RequestContext,
        message: str,
    ) -> ChatResponse:
        if decision.rejection or decision.cancel_requested:
            state.pending_confirmation = None
            state.pending_intents = []
            state.scene_status = SceneStatus.CANCELLED
            state.current_step = "action_cancelled"
            return self._response(
                state, context.trace_id, self.responses.render(state.language, "action_rejected")
            )
        if decision.waybill_no or decision.new_address or decision.modifies_existing:
            state.pending_confirmation = None
            state.scene_status = SceneStatus.COLLECTING
            return await self._dispatch(state, decision, context, message)
        if not decision.confirmation:
            if (
                state.pending_confirmation
                and state.pending_confirmation.tool == "urge_delivery"
                and decision.intent == Intent.DELIVERY_FOLLOWUP
                and decision.explicit_intent
            ):
                # A second explicit request such as "能快些吗" confirms the shown follow-up.
                return await self._execute_pending(state, context)
            return self._response(
                state,
                context.trace_id,
                self.responses.render(state.language, "repeat_confirmation"),
                action_required="confirm_action",
                data={
                    "tool": state.pending_confirmation.tool,
                    "waybill_no": state.pending_confirmation.arguments.get("waybill_no"),
                },
            )
        return await self._execute_pending(state, context)

    async def _execute_pending(
        self, state: ConversationState, context: RequestContext
    ) -> ChatResponse:
        pending = state.pending_confirmation
        if pending is None:
            state.reset_scene()
            return self._response(
                state, context.trace_id, self.responses.render(state.language, "clarify")
            )
        args = pending.arguments
        waybill = str(args["waybill_no"])

        if pending.tool == "create_complaint":
            tracking = await self.tools.query_tracking(
                QueryTrackingInput(waybill_no=waybill), context
            )
            if not tracking.succeeded:
                return await self._tool_failure(state, tracking, context)
            if args.get("complaint_type") == "delivered_not_received":
                if tracking.data.get("status") != "delivered":
                    state.pending_confirmation = None
                    self.scenes.complete(state)
                    tracking_values = self.responses.tracking_values(state.language, tracking.data)
                    reply = self.responses.render(
                        state.language,
                        "not_delivered",
                        **tracking_values,
                    )
                    return self._response(state, context.trace_id, reply)
                verification = await self.tools.verify_receiver(
                    VerifyReceiverInput(
                        waybill_no=waybill,
                        phone_last4=str(args.get("phone_last4", "")),
                    ),
                    context,
                )
                if not verification.succeeded:
                    return await self._tool_failure(state, verification, context)
                if not verification.data.get("verified"):
                    state.pending_confirmation = None
                    state.scene_status = SceneStatus.FAILED
                    return self._response(
                        state,
                        context.trace_id,
                        self.responses.render(state.language, "identity_failed"),
                    )
            result = await self.tools.create_complaint(
                CreateComplaintInput(
                    waybill_no=waybill,
                    complaint_type=str(args["complaint_type"]),
                    description=str(args["description"]),
                    idempotency_key=pending.idempotency_key,
                ),
                context,
            )
            if not result.succeeded:
                return await self._tool_failure(state, result, context)
            state.last_tool_result = result.data
            state.slots["ticket_id"] = result.data["ticket_id"]
            state.remember_ticket(result.data["ticket_id"])
            self.scenes.complete(state)
            reply = self.responses.render(
                state.language, "complaint_created", ticket_id=result.data["ticket_id"]
            )
            return self._response(
                state,
                context.trace_id,
                reply,
                data={"ticket_id": result.data["ticket_id"], "status": result.data["status"]},
            )

        if pending.tool == "urge_delivery":
            tracking = await self.tools.query_tracking(
                QueryTrackingInput(waybill_no=waybill), context
            )
            if not tracking.succeeded:
                return await self._tool_failure(state, tracking, context)
            if not tracking.data.get("can_urge"):
                state.pending_confirmation = None
                self.scenes.complete(state)
                tracking_values = self.responses.tracking_values(state.language, tracking.data)
                reply = self.responses.render(
                    state.language,
                    "followup_unavailable",
                    waybill=waybill,
                    **tracking_values,
                )
                return self._response(state, context.trace_id, reply)
            result = await self.tools.urge_delivery(
                UrgeDeliveryInput(
                    waybill_no=waybill,
                    reason=str(args["reason"]),
                    idempotency_key=pending.idempotency_key,
                ),
                context,
            )
            if not result.succeeded:
                return await self._tool_failure(state, result, context)
            state.last_tool_result = result.data
            state.slots["ticket_id"] = result.data["ticket_id"]
            state.remember_ticket(result.data["ticket_id"])
            self.scenes.complete(state)
            reply = self.responses.render(
                state.language, "followup_created", ticket_id=result.data["ticket_id"]
            )
            return self._response(state, context.trace_id, reply, data=result.data)

        eligibility = await self.tools.check_address_change(
            CheckAddressChangeInput(waybill_no=waybill), context
        )
        if not eligibility.succeeded:
            return await self._tool_failure(state, eligibility, context)
        if not eligibility.data.get("can_change"):
            state.pending_confirmation = None
            self.scenes.complete(state)
            address_values = self.responses.address_unavailable_values(
                state.language,
                eligibility.data,
            )
            reply = self.responses.render(
                state.language,
                "address_unavailable",
                **address_values,
            )
            return self._response(state, context.trace_id, reply)
        result = await self.tools.change_address(
            ChangeAddressInput(
                waybill_no=waybill,
                new_address=str(args["new_address"]),
                idempotency_key=pending.idempotency_key,
            ),
            context,
        )
        if not result.succeeded:
            return await self._tool_failure(state, result, context)
        state.last_tool_result = result.data
        self.scenes.complete(state)
        reply = self.responses.render(
            state.language, "address_changed", request_id=result.data["request_id"]
        )
        return self._response(state, context.trace_id, reply, data=result.data)

    async def _tool_failure(
        self, state: ConversationState, result: ToolResult, context: RequestContext
    ) -> ChatResponse:
        state.retry_count += 1
        state.pending_intents = []
        state.scene_status = SceneStatus.FAILED
        state.current_step = "retry_tool"
        state.last_tool_result = {
            "status": "failed",
            "error_code": result.error_code,
            "retryable": result.retryable,
        }
        if state.retry_count >= self.settings.max_scene_retries:
            return await self._transfer(state, context, reason="repeated_tool_failure")
        return self._response(
            state,
            context.trace_id,
            self.responses.render(
                state.language, "tool_failed", error_code=result.error_code or "TOOL_ERROR"
            ),
            action_required="retry_or_contact_human",
        )

    async def _transfer(
        self, state: ConversationState, context: RequestContext, *, reason: str
    ) -> ChatResponse:
        result = await self.tools.transfer_to_human(TransferToHumanInput(reason=reason), context)
        state.pending_confirmation = None
        state.pending_intents = []
        state.scene_status = SceneStatus.TRANSFER
        state.current_step = "human_queue"
        if result.succeeded:
            queue_id = result.data.get("queue_id", "pending")
            reply = self.responses.render(state.language, "transfer", queue_id=queue_id)
            return self._response(
                state,
                context.trace_id,
                reply,
                action_required="contact_human",
                data={"queue_id": queue_id},
            )
        return self._response(
            state,
            context.trace_id,
            self.responses.render(
                state.language, "tool_failed", error_code=result.error_code or "TRANSFER_FAILED"
            ),
            action_required="contact_human",
        )

    def _ask_waybill(self, state: ConversationState, trace_id: str) -> ChatResponse:
        self.scenes.collect(state, "waiting_waybill")
        reply = self.responses.render(state.language, "ask_waybill")
        if state.current_intent and state.pending_intents:
            reply = self.responses.planned_prompt(
                state.language,
                state.current_intent,
                [item.intent for item in state.pending_intents],
                reply,
            )
        return self._response(
            state,
            trace_id,
            reply,
            action_required="provide_waybill_no",
            data={
                "planned_intents": [
                    intent.value
                    for intent in [
                        state.current_intent,
                        *[item.intent for item in state.pending_intents],
                    ]
                ]
                if state.current_intent and state.pending_intents
                else [],
            },
        )

    def _invalid_waybill(
        self,
        state: ConversationState,
        trace_id: str,
        candidate: str,
        intent: Intent | None,
    ) -> ChatResponse:
        self.scenes.activate(
            state,
            intent or state.current_intent or Intent.TRACKING,
            preserve_pending_intents=True,
        )
        state.remember_waybill(candidate, valid=False)
        self.scenes.collect(state, "waiting_waybill")
        return self._response(
            state,
            trace_id,
            self.responses.render(state.language, "invalid_waybill", waybill=candidate),
            action_required="provide_waybill_no",
            data={"invalid_waybill_no": candidate},
        )

    @staticmethod
    def _safe_tracking(data: dict[str, Any]) -> dict[str, Any]:
        return {
            key: data.get(key)
            for key in (
                "waybill_no",
                "status",
                "current_node",
                "can_urge",
                "eta",
                "exception",
                "events",
                "pod",
            )
        }

    @staticmethod
    def _complaint_type(message: str) -> str:
        lower = message.lower()
        if any(token in lower for token in ("破损", "damaged", "hu hong", "hư hỏng")):
            return "damaged_parcel"
        if any(token in lower for token in ("遗失", "丢失", "lost", "that lac", "thất lạc")):
            return "lost_parcel"
        if any(
            token in lower
            for token in ("理赔", "claim", "compensation", "boi thuong", "bồi thường")
        ):
            return "claim_request"
        return "general_complaint"

    @staticmethod
    def _response(
        state: ConversationState,
        trace_id: str,
        reply: str,
        *,
        action_required: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> ChatResponse:
        return ChatResponse(
            reply=reply,
            status=state.scene_status,
            current_intent=state.current_intent,
            current_step=state.current_step,
            action_required=action_required,
            data=data or {},
            trace_id=trace_id,
        )
