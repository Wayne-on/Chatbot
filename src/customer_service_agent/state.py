from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from customer_service_agent.schemas import (
    ConversationMessage,
    Intent,
    PendingConfirmation,
    PendingIntent,
    SceneStatus,
)

MAX_RECENT_MESSAGES = 12
MAX_IDENTIFIER_HISTORY = 20


def initial_slots() -> dict[str, Any]:
    return {
        "waybill_no": None,
        "phone_last4": None,
        "contact_phone": None,
        "complaint_type": None,
        "complaint_description": None,
        "new_address": None,
        "date_range_start": None,
        "date_range_end": None,
        "include_details": None,
        "ticket_id": None,
    }


class ConversationState(BaseModel):
    current_intent: Intent | None = None
    current_step: str | None = None
    scene_status: SceneStatus = SceneStatus.IDLE
    slots: dict[str, Any] = Field(default_factory=initial_slots)
    scene_context: dict[str, Any] = Field(default_factory=dict)
    last_tool_result: dict[str, Any] | None = None
    pending_confirmation: PendingConfirmation | None = None
    pending_intents: list[PendingIntent] = Field(default_factory=list)
    retry_count: int = 0
    language: str = "en"
    owner_hash: str | None = None
    recent_messages: list[ConversationMessage] = Field(default_factory=list)
    waybill_history: list[str] = Field(default_factory=list)
    valid_waybill_history: list[str] = Field(default_factory=list)
    ticket_history: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def active(self) -> bool:
        return self.scene_status in {
            SceneStatus.COLLECTING,
            SceneStatus.PROCESSING,
            SceneStatus.WAITING_CONFIRMATION,
        }

    def reset_scene(
        self,
        *,
        status: SceneStatus = SceneStatus.IDLE,
        preserve_pending_intents: bool = False,
    ) -> None:
        self.current_intent = None
        self.current_step = None
        self.scene_status = status
        self.slots = initial_slots()
        self.scene_context = {}
        self.pending_confirmation = None
        if not preserve_pending_intents:
            self.pending_intents = []
        self.retry_count = 0
        self.updated_at = datetime.now(UTC)

    @property
    def last_waybill_no(self) -> str | None:
        return self.valid_waybill_history[-1] if self.valid_waybill_history else None

    @property
    def last_ticket_id(self) -> str | None:
        return self.ticket_history[-1] if self.ticket_history else None

    def remember_waybill(self, waybill_no: str, *, valid: bool = True) -> None:
        normalized = waybill_no.strip().upper()
        if normalized and normalized not in self.waybill_history:
            self.waybill_history.append(normalized)
            self.waybill_history = self.waybill_history[-MAX_IDENTIFIER_HISTORY:]
        if valid and normalized and normalized not in self.valid_waybill_history:
            self.valid_waybill_history.append(normalized)
            self.valid_waybill_history = self.valid_waybill_history[-MAX_IDENTIFIER_HISTORY:]

    def remember_ticket(self, ticket_id: str) -> None:
        normalized = ticket_id.strip().upper()
        if normalized and normalized not in self.ticket_history:
            self.ticket_history.append(normalized)
            self.ticket_history = self.ticket_history[-MAX_IDENTIFIER_HISTORY:]

    def append_turn(self, user_message: str, assistant_reply: str) -> None:
        """Keep the latest six user/assistant exchanges, matching the source DSL window."""
        self.recent_messages.extend(
            [
                ConversationMessage(role="user", content=user_message[:4000]),
                ConversationMessage(role="assistant", content=assistant_reply[:4000]),
            ]
        )
        self.recent_messages = self.recent_messages[-MAX_RECENT_MESSAGES:]
        self.updated_at = datetime.now(UTC)

    def clear_for_new_owner(self) -> None:
        """Clear all conversational data when a session identifier changes owner."""
        self.reset_scene()
        self.last_tool_result = None
        self.recent_messages = []
        self.waybill_history = []
        self.valid_waybill_history = []
        self.ticket_history = []


class ConversationCheckpointer(Protocol):
    async def get(self, thread_id: str) -> ConversationState: ...

    async def put(self, thread_id: str, state: ConversationState) -> None: ...

    async def clear(self, thread_id: str) -> None: ...

    def session(self, thread_id: str) -> AsyncIterator[ConversationState]: ...


class InMemoryConversationCheckpointer:
    """Process-local checkpointer with per-thread serialization and copy isolation."""

    def __init__(self) -> None:
        self._states: dict[str, ConversationState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _lock_for(self, thread_id: str) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(thread_id, asyncio.Lock())

    async def get(self, thread_id: str) -> ConversationState:
        async with self._guard:
            state = self._states.get(thread_id, ConversationState())
            return state.model_copy(deep=True)

    async def put(self, thread_id: str, state: ConversationState) -> None:
        state.updated_at = datetime.now(UTC)
        async with self._guard:
            self._states[thread_id] = state.model_copy(deep=True)

    async def clear(self, thread_id: str) -> None:
        async with self._guard:
            self._states.pop(thread_id, None)

    @asynccontextmanager
    async def session(self, thread_id: str) -> AsyncIterator[ConversationState]:
        lock = await self._lock_for(thread_id)
        async with lock:
            state = await self.get(thread_id)
            try:
                yield state
            finally:
                await self.put(thread_id, state)
