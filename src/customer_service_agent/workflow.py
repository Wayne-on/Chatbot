from __future__ import annotations

from time import perf_counter
from typing import Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from customer_service_agent.schemas import (
    ChatRequest,
    ChatResponse,
    RequestContext,
    RouteDecision,
    SceneStatus,
)
from customer_service_agent.services.conversation_service import ConversationService
from customer_service_agent.state import ConversationState


class CustomerServiceGraphState(TypedDict, total=False):
    """One LangGraph run; durable conversation data lives in ``conversation``."""

    request: ChatRequest
    trace_id: str
    started: float
    conversation: ConversationState
    context: RequestContext
    rule_decision: RouteDecision
    model_decision: RouteDecision | None
    decision: RouteDecision
    response: ChatResponse


class CustomerServiceWorkflow:
    """Outer LangGraph controlling deterministic and model-assisted customer-service nodes."""

    def __init__(self, service: ConversationService) -> None:
        self.service = service
        self.graph = self._build_graph()

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(CustomerServiceGraphState)
        builder.add_node("load_session", self._load_session)
        builder.add_node("deterministic_router", self._deterministic_router)
        builder.add_node("semantic_router", self._semantic_router)
        builder.add_node("resolve_decision", self._resolve_decision)
        builder.add_node("execute_business", self._execute_business)
        builder.add_node("response_writer", self._response_writer)
        builder.add_node("save_turn", self._save_turn)

        builder.add_edge(START, "load_session")
        builder.add_edge("load_session", "deterministic_router")
        builder.add_conditional_edges(
            "deterministic_router",
            self._routing_path,
            {
                "semantic_router": "semantic_router",
                "resolve_decision": "resolve_decision",
            },
        )
        builder.add_edge("semantic_router", "resolve_decision")
        builder.add_edge("resolve_decision", "execute_business")
        builder.add_edge("execute_business", "response_writer")
        builder.add_edge("response_writer", "save_turn")
        builder.add_edge("save_turn", END)
        return builder.compile()

    async def ainvoke(
        self,
        request: ChatRequest,
        *,
        trace_id: str | None = None,
    ) -> ChatResponse:
        trace_id = trace_id or uuid4().hex
        async with self.service.checkpointer.session(request.session_id) as conversation:
            result = await self.graph.ainvoke(
                {
                    "request": request,
                    "trace_id": trace_id,
                    "started": perf_counter(),
                    "conversation": conversation,
                }
            )
            updated = ConversationState.model_validate(result["conversation"])
            if updated is not conversation:
                for field_name in ConversationState.model_fields:
                    setattr(conversation, field_name, getattr(updated, field_name))
            return ChatResponse.model_validate(result["response"])

    async def _load_session(self, state: CustomerServiceGraphState) -> CustomerServiceGraphState:
        request = state["request"]
        conversation = state["conversation"]
        self.service.prepare_state(conversation, request.user_id)
        return {
            "conversation": conversation,
            "context": self.service.build_context(request, state["trace_id"]),
        }

    async def _deterministic_router(
        self, state: CustomerServiceGraphState
    ) -> CustomerServiceGraphState:
        return {
            "rule_decision": self.service.route_deterministically(
                state["request"],
                state["conversation"],
            )
        }

    def _routing_path(
        self, state: CustomerServiceGraphState
    ) -> Literal["semantic_router", "resolve_decision"]:
        if self.service.should_use_model_router(
            state["conversation"],
            state["rule_decision"],
            state["request"].message,
        ):
            return "semantic_router"
        return "resolve_decision"

    async def _semantic_router(self, state: CustomerServiceGraphState) -> CustomerServiceGraphState:
        model_router = self.service.model_router
        if model_router is None:
            return {"model_decision": None}
        decision = await model_router(
            state["request"].message,
            state["conversation"],
            state["context"],
        )
        return {"model_decision": decision}

    async def _resolve_decision(
        self, state: CustomerServiceGraphState
    ) -> CustomerServiceGraphState:
        return {
            "decision": self.service.resolve_decision(
                state["rule_decision"],
                state.get("model_decision"),
            )
        }

    async def _execute_business(
        self, state: CustomerServiceGraphState
    ) -> CustomerServiceGraphState:
        response = await self.service.execute_decision(
            state["request"],
            state["conversation"],
            state["context"],
            state["decision"],
            started=state["started"],
            generate_reply=False,
            finalize=False,
        )
        return {
            "conversation": state["conversation"],
            "response": response,
        }

    async def _response_writer(self, state: CustomerServiceGraphState) -> CustomerServiceGraphState:
        response = state["response"]
        generator = self.service.response_generator
        if (
            generator is not None
            and response.status == SceneStatus.COMPLETED
            and response.action_required is None
        ):
            generated = await generator(
                state["request"].message,
                state["conversation"],
                response,
                state["context"],
            )
            if generated:
                response = response.model_copy(update={"reply": generated})
        return {"response": response}

    async def _save_turn(self, state: CustomerServiceGraphState) -> CustomerServiceGraphState:
        self.service.finalize_turn(
            state["request"],
            state["conversation"],
            state["response"],
            state["context"],
            started=state["started"],
        )
        return {
            "conversation": state["conversation"],
            "response": state["response"],
        }
