import asyncio
from types import SimpleNamespace

import httpx

from customer_service_agent.adapters.mock_backend import MockBackend
from customer_service_agent.config import Settings
from customer_service_agent.main import create_app
from customer_service_agent.spike.schemas import SpikeRunStatus, SpikeScenario
from customer_service_agent.spike.service import DeepAgentsSpikeService
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
                {"todos": [{"content": "等待资料", "status": "in_progress"}]},
                [
                    SimpleNamespace(
                        value={
                            "action_requests": [
                                {
                                    "name": "request_information",
                                    "args": {"question": "请补充资料"},
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
                "todos": [{"content": "等待资料", "status": "completed"}],
                "messages": [SimpleNamespace(type="ai", content="任务已经安全完成。")],
            }
        )


def build_spike_service(agent: FakePauseThenCompleteAgent) -> DeepAgentsSpikeService:
    return DeepAgentsSpikeService(
        settings=Settings(
            _env_file=None,
            model_name=None,
            model_api_key=None,
            spike_task_timeout_seconds=30,
        ),
        business_tools=BusinessTools(MockBackend()),
        model=None,
        compiled_agents={SpikeScenario.MULTI_PARCEL_RESOLUTION: agent},
    )


async def test_spike_api_returns_503_without_model(container) -> None:
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/deep-agent/runs",
            json={
                "session_id": "api-spike-disabled",
                "user_id": "user-1",
                "scenario": "crossborder_customs",
                "message": "处理 CB-VN-CN-001",
                "language": "zh-CN",
            },
        )
    assert response.status_code == 503


async def test_spike_api_create_poll_and_resume(container) -> None:
    container.spike_service = build_spike_service(FakePauseThenCompleteAgent())
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/deep-agent/runs",
            json={
                "session_id": "api-spike-1",
                "user_id": "user-1",
                "scenario": SpikeScenario.MULTI_PARCEL_RESOLUTION.value,
                "message": "处理 ORD-DA-001",
                "language": "zh-CN",
            },
        )
        assert created.status_code == 202
        accepted = created.json()
        headers = {"X-Spike-Access-Token": accepted["access_token"]}

        snapshot = None
        for _ in range(100):
            response = await client.get(
                f"/v1/deep-agent/runs/{accepted['run_id']}", headers=headers
            )
            snapshot = response.json()
            if snapshot["status"] == SpikeRunStatus.WAITING_INPUT.value:
                break
            await asyncio.sleep(0.01)
        assert snapshot is not None
        assert snapshot["status"] == "waiting_input"

        unauthorized = await client.get(f"/v1/deep-agent/runs/{accepted['run_id']}")
        assert unauthorized.status_code == 422
        resumed = await client.post(
            f"/v1/deep-agent/runs/{accepted['run_id']}/resume",
            json={
                "access_token": accepted["access_token"],
                "checkpoint_version": snapshot["checkpoint_version"],
                "decision": "respond",
                "message": "新地址是 123 Nguyen Hue Street, District 1，手机号后四位 1234",
            },
        )
        assert resumed.status_code == 202

        for _ in range(100):
            response = await client.get(
                f"/v1/deep-agent/runs/{accepted['run_id']}", headers=headers
            )
            snapshot = response.json()
            if snapshot["status"] == SpikeRunStatus.COMPLETED.value:
                break
            await asyncio.sleep(0.01)
    assert snapshot["status"] == "completed"
    assert snapshot["reply"] == "任务已经安全完成。"
