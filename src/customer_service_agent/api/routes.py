from fastapi import APIRouter, Header, HTTPException, Request, status

from customer_service_agent.api.dependencies import Container
from customer_service_agent.schemas import ChatRequest, ChatResponse, HealthResponse
from customer_service_agent.spike.schemas import (
    SpikeRunAccepted,
    SpikeRunCreateRequest,
    SpikeRunResumeRequest,
    SpikeRunSnapshot,
)
from customer_service_agent.spike.service import (
    SpikeCheckpointConflictError,
    SpikeRunConflictError,
    SpikeRunNotFoundError,
    SpikeUnavailableError,
)

router = APIRouter()


def _container(request: Request) -> Container:
    return request.app.state.container


@router.post("/v1/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    container = _container(request)
    return await container.agent.ainvoke(body, trace_id=request.state.trace_id)


@router.post(
    "/v1/deep-agent/runs",
    response_model=SpikeRunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_deep_agent_run(body: SpikeRunCreateRequest, request: Request) -> SpikeRunAccepted:
    try:
        return await _container(request).spike_service.create_run(
            body, trace_id=request.state.trace_id
        )
    except SpikeUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except SpikeRunConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/v1/deep-agent/runs/{run_id}", response_model=SpikeRunSnapshot)
async def get_deep_agent_run(
    run_id: str,
    request: Request,
    access_token: str = Header(alias="X-Spike-Access-Token"),
) -> SpikeRunSnapshot:
    try:
        return await _container(request).spike_service.get_snapshot(run_id, access_token)
    except SpikeRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc


@router.post(
    "/v1/deep-agent/runs/{run_id}/resume",
    response_model=SpikeRunSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_deep_agent_run(
    run_id: str, body: SpikeRunResumeRequest, request: Request
) -> SpikeRunSnapshot:
    try:
        return await _container(request).spike_service.resume_run(run_id, body)
    except SpikeRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc
    except (SpikeRunConflictError, SpikeCheckpointConflictError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/v1/deep-agent/runs/{run_id}", response_model=SpikeRunSnapshot)
async def cancel_deep_agent_run(
    run_id: str,
    request: Request,
    access_token: str = Header(alias="X-Spike-Access-Token"),
) -> SpikeRunSnapshot:
    try:
        return await _container(request).spike_service.cancel_run(run_id, access_token)
    except SpikeRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc


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
