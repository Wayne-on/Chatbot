import pytest

from customer_service_agent.adapters.mock_backend import MockBackend
from customer_service_agent.api.dependencies import Container, build_container
from customer_service_agent.config import Settings


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend()


@pytest.fixture
def container(backend: MockBackend) -> Container:
    return build_container(Settings(model_name=None, model_api_key=None), backend)
