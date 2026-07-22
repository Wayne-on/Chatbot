from __future__ import annotations

from dataclasses import dataclass

from customer_service_agent.adapters.base import BusinessBackend
from customer_service_agent.adapters.http_backend import HttpBackend
from customer_service_agent.adapters.mock_backend import MockBackend
from customer_service_agent.agent import CustomerServiceAgent
from customer_service_agent.config import Settings
from customer_service_agent.router import Router
from customer_service_agent.services.conversation_service import ConversationService
from customer_service_agent.services.response_service import ResponseService
from customer_service_agent.services.scene_manager import SceneManager
from customer_service_agent.spike.service import DeepAgentsSpikeService
from customer_service_agent.state import InMemoryConversationCheckpointer
from customer_service_agent.tools.service import BusinessTools


@dataclass(slots=True)
class Container:
    settings: Settings
    backend: BusinessBackend
    tools: BusinessTools
    checkpointer: InMemoryConversationCheckpointer
    agent: CustomerServiceAgent
    spike_service: DeepAgentsSpikeService


def build_container(
    settings: Settings | None = None, backend: BusinessBackend | None = None
) -> Container:
    settings = settings or Settings()
    if backend is None:
        if settings.business_backend == "http":
            token = settings.business_service_token
            backend = HttpBackend(
                base_url=settings.business_api_base_url or "",
                service_token=token.get_secret_value() if token else None,
                query_max_retries=settings.business_query_max_retries,
            )
        else:
            backend = MockBackend()
    tools = BusinessTools(backend)
    checkpointer = InMemoryConversationCheckpointer()
    service = ConversationService(
        settings=settings,
        checkpointer=checkpointer,
        router=Router(),
        tools=tools,
        responses=ResponseService(),
        scenes=SceneManager(),
    )
    agent = CustomerServiceAgent(service=service, settings=settings, tools=tools)
    # The framework Spike is intentionally demo-only. Even when the stable chat uses the
    # HTTP adapter, Spike writes must stay inside deterministic local Mock systems.
    spike_service = DeepAgentsSpikeService(
        settings=settings,
        business_tools=BusinessTools(MockBackend()),
        model=agent.response_model,
    )
    return Container(
        settings=settings,
        backend=backend,
        tools=tools,
        checkpointer=checkpointer,
        agent=agent,
        spike_service=spike_service,
    )
