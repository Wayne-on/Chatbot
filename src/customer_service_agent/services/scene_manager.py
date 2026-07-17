import hashlib

from customer_service_agent.schemas import Intent, PendingConfirmation, RequestContext, SceneStatus
from customer_service_agent.state import ConversationState


class SceneManager:
    def activate(
        self,
        state: ConversationState,
        intent: Intent,
        *,
        preserve_pending_intents: bool = False,
    ) -> None:
        if state.current_intent != intent:
            state.reset_scene(preserve_pending_intents=preserve_pending_intents)
            state.current_intent = intent
        elif state.scene_status in {SceneStatus.COMPLETED, SceneStatus.CANCELLED}:
            state.scene_status = SceneStatus.IDLE
        state.current_intent = intent

    def collect(self, state: ConversationState, step: str) -> None:
        state.scene_status = SceneStatus.COLLECTING
        state.current_step = step

    def complete(self, state: ConversationState) -> None:
        state.scene_status = SceneStatus.COMPLETED
        state.current_step = "completed"
        state.pending_confirmation = None
        state.retry_count = 0

    def set_pending(
        self,
        state: ConversationState,
        *,
        tool: str,
        arguments: dict[str, object],
        prompt_key: str,
        context: RequestContext,
    ) -> PendingConfirmation:
        waybill = str(arguments.get("waybill_no", ""))
        raw = f"{context.session_id}|{context.user_id}|{context.request_id}|{tool}|{waybill}"
        idempotency_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        pending = PendingConfirmation(
            tool=tool,
            arguments=arguments,
            idempotency_key=idempotency_key,
            prompt_key=prompt_key,
        )
        state.pending_confirmation = pending
        state.scene_status = SceneStatus.WAITING_CONFIRMATION
        state.current_step = "waiting_confirmation"
        return pending
