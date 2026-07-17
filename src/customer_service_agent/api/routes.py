from fastapi import APIRouter, HTTPException, Request, status

from customer_service_agent.api.dependencies import Container
from customer_service_agent.schemas import ChatRequest, ChatResponse, HealthResponse

router = APIRouter()


def _container(request: Request) -> Container:
    return request.app.state.container


@router.post("/v1/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    container = _container(request)
    return await container.agent.ainvoke(body, trace_id=request.state.trace_id)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    container = _container(request)
    if not await container.backend.ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="business backend is not ready",
        )
    return HealthResponse(status="ready")
