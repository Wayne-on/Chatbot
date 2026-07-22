import asyncio
from types import SimpleNamespace

import pytest

from customer_service_agent.adapters.mock_backend import MockBackend
from customer_service_agent.config import Settings
from customer_service_agent.spike.schemas import (
    SpikeRunCreateRequest,
    SpikeRunResumeRequest,
    SpikeRunStatus,
    SpikeScenario,
)
from customer_service_agent.spike.service import (
    DeepAgentsSpikeService,
    SpikeCheckpointConflictError,
    SpikeRunConflictError,
    SpikeRunNotFoundError,
)
from customer_service_agent.tools.service import BusinessTools


class FakeGraphOutput:
    def __init__(self, value, interrupts=None):
        self.value = value
        self.interrupts = interrupts or []


class FakePauseThenCompleteAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, *_args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return FakeGraphOutput(
                {
                    "todos": [
                        {"content": "调查多个系统", "status": "completed"},
                        {"content": "等待用户资料", "status": "in_progress"},
                    ]
                },
                interrupts=[
                    SimpleNamespace(
                        value={
                            "action_requests": [
                                {
                                    "name": "request_information",
                                    "args": {
                                        "question": "请补充资料",
                                        "requested_fields": ["document_ids"],
                                    },
                                    "description": "need documents",
                                }
                            ],
                            "review_configs": [
                                {
                                    "action_name": "request_information",
                                    "allowed_decisions": ["respond"],
                                }
                            ],
                        }
                    )
                ],
            )
        context = kwargs["context"]
        context.facts["verified_waybills"] = {"JT100000005"}
        context.facts["address_change_request_id"] = "ADR-TEST"
        context.facts["delivery_followup_ticket_id"] = "URG-TEST"
        context.facts["missing_delivery_ticket_id"] = "CMP-TEST"
        context.facts["outlet_hold_request_id"] = "HOLD-TEST"
        return FakeGraphOutput(
            {
                "todos": [
                    {"content": "调查多个系统", "status": "completed"},
                    {"content": "等待用户资料", "status": "completed"},
                ],
                "messages": [SimpleNamespace(type="ai", content="任务已经安全完成。")],
            }
        )


def build_spike_service(agent: FakePauseThenCompleteAgent) -> DeepAgentsSpikeService:
    settings = Settings(
        _env_file=None,
        model_name=None,
        model_api_key=None,
        spike_task_timeout_seconds=30,
    )
    return DeepAgentsSpikeService(
        settings=settings,
        business_tools=BusinessTools(MockBackend()),
        model=None,
        compiled_agents={SpikeScenario.MULTI_PARCEL_RESOLUTION: agent},
    )


async def wait_for_status(
    service: DeepAgentsSpikeService,
    run_id: str,
    token: str,
    expected: set[SpikeRunStatus],
):
    for _ in range(100):
        snapshot = await service.get_snapshot(run_id, token)
        if snapshot.status in expected:
            return snapshot
        await asyncio.sleep(0.01)
    raise AssertionError("Spike run did not reach expected status")


async def test_spike_pauses_resumes_and_keeps_owner_boundary() -> None:
    agent = FakePauseThenCompleteAgent()
    service = build_spike_service(agent)
    accepted = await service.create_run(
        SpikeRunCreateRequest(
            session_id="spike-unit-1",
            user_id="user-1",
            scenario=SpikeScenario.MULTI_PARCEL_RESOLUTION,
            message="处理 ORD-DA-001",
        )
    )
    paused = await wait_for_status(
        service,
        accepted.run_id,
        accepted.access_token,
        {SpikeRunStatus.WAITING_INPUT},
    )
    assert paused.pending_action is not None
    assert paused.pending_action.kind == "input"
    assert paused.plan[1].status == "in_progress"

    with pytest.raises(SpikeRunNotFoundError):
        await service.get_snapshot(accepted.run_id, "wrong-access-token-value")
    with pytest.raises(SpikeCheckpointConflictError):
        await service.resume_run(
            accepted.run_id,
            SpikeRunResumeRequest(
                access_token=accepted.access_token,
                checkpoint_version=paused.checkpoint_version + 1,
                decision="respond",
                message="新地址是 123 Nguyen Hue Street, District 1，手机号后四位 1234",
            ),
        )

    await service.resume_run(
        accepted.run_id,
        SpikeRunResumeRequest(
            access_token=accepted.access_token,
            checkpoint_version=paused.checkpoint_version,
            decision="respond",
            message="新地址是 123 Nguyen Hue Street, District 1，手机号后四位 1234",
        ),
    )
    completed = await wait_for_status(
        service,
        accepted.run_id,
        accepted.access_token,
        {SpikeRunStatus.COMPLETED},
    )
    assert completed.reply == "任务已经安全完成。"
    assert completed.plan[-1].status == "completed"
    assert [event.event_type for event in completed.events].count("run_resumed") == 1
    await service.shutdown()


async def test_spike_allows_only_one_active_run_per_session() -> None:
    service = build_spike_service(FakePauseThenCompleteAgent())
    body = SpikeRunCreateRequest(
        session_id="same-session",
        user_id="same-user",
        scenario=SpikeScenario.MULTI_PARCEL_RESOLUTION,
        message="处理 ORD-DA-001",
    )
    accepted = await service.create_run(body)
    with pytest.raises(SpikeRunConflictError):
        await service.create_run(body)
    await service.cancel_run(accepted.run_id, accepted.access_token)
    await service.shutdown()


async def test_customs_runtime_guard_prevents_prose_only_early_completion() -> None:
    agent = FakePauseThenCompleteAgent()
    agent.calls = 1
    settings = Settings(
        _env_file=None,
        model_name=None,
        model_api_key=None,
        spike_task_timeout_seconds=30,
    )
    service = DeepAgentsSpikeService(
        settings=settings,
        business_tools=BusinessTools(MockBackend()),
        model=None,
        compiled_agents={SpikeScenario.CROSSBORDER_CUSTOMS: agent},
    )
    accepted = await service.create_run(
        SpikeRunCreateRequest(
            session_id="customs-guard",
            user_id="user-1",
            scenario=SpikeScenario.CROSSBORDER_CUSTOMS,
            message="处理 CB-VN-CN-001",
        )
    )

    paused = await wait_for_status(
        service,
        accepted.run_id,
        accepted.access_token,
        {SpikeRunStatus.WAITING_INPUT},
    )
    assert paused.pending_action is not None
    assert paused.pending_action.actions[0]["name"] == "request_information"
    assert "DOC-BAT-VALID" in paused.reply
    assert any(event.event_type == "run_paused" for event in paused.events)
    assert all(event.event_type != "run_completed" for event in paused.events)
    await service.cancel_run(accepted.run_id, accepted.access_token)
    await service.shutdown()
