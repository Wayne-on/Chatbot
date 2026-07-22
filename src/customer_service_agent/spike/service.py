from __future__ import annotations

import asyncio
import hmac
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from customer_service_agent.config import Settings
from customer_service_agent.spike.backend import MockSpikeBackend
from customer_service_agent.spike.schemas import (
    SpikeEvent,
    SpikePendingAction,
    SpikePlanItem,
    SpikeRunAccepted,
    SpikeRunCreateRequest,
    SpikeRunResumeRequest,
    SpikeRunSnapshot,
    SpikeRunStatus,
    SpikeScenario,
)
from customer_service_agent.spike.tools import (
    CUSTOMS_READ_TOOLS,
    CUSTOMS_WRITE_TOOLS,
    ORDER_READ_TOOLS,
    ORDER_WRITE_TOOLS,
    SpikeAgentContext,
    inspect_document_bundle,
    quote_customs_options,
    request_compliance_decision,
    request_information,
    submit_compliance_review,
    verify_receiver,
)
from customer_service_agent.tools.service import BusinessTools

logger = logging.getLogger(__name__)

SPIKE_ROOT = Path(__file__).resolve().parent
SPIKE_SKILLS_ROOT = SPIKE_ROOT / "skills"

SCENARIO_TITLES = {
    SpikeScenario.MULTI_PARCEL_RESOLUTION: "一单多包裹综合异常处理",
    SpikeScenario.CROSSBORDER_CUSTOMS: "跨境海关异常材料与决策",
}

INPUT_ACTIONS = {"request_information", "request_compliance_decision"}


class SpikeUnavailableError(RuntimeError):
    pass


class SpikeRunNotFoundError(LookupError):
    pass


class SpikeRunConflictError(RuntimeError):
    pass


class SpikeCheckpointConflictError(RuntimeError):
    pass


@dataclass
class _RunRecord:
    run_id: str
    access_token: str
    session_id: str
    user_id: str
    scenario: SpikeScenario
    objective: str
    language: str
    trace_id: str
    status: SpikeRunStatus = SpikeRunStatus.QUEUED
    reply: str | None = None
    plan: list[SpikePlanItem] = field(default_factory=list)
    events: list[SpikeEvent] = field(default_factory=list)
    next_event_sequence: int = 1
    tool_evidence_count: int = 0
    reference_ids: list[str] = field(default_factory=list)
    pending_action: SpikePendingAction | None = None
    pending_resume_mode: str = "command"
    checkpoint_version: int = 0
    result: dict[str, Any] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    allowed_waybills: set[str] = field(default_factory=set)
    allowed_case_ids: set[str] = field(default_factory=set)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DeepAgentsSpikeService:
    """Process-local Spike runner for complex, pauseable DeepAgents tasks."""

    def __init__(
        self,
        *,
        settings: Settings,
        business_tools: BusinessTools,
        model: Any | None,
        compiled_agents: dict[SpikeScenario, Any] | None = None,
        spike_backend: MockSpikeBackend | None = None,
    ) -> None:
        self.settings = settings
        self.business_tools = business_tools
        self.spike_backend = spike_backend or MockSpikeBackend()
        self._records: dict[str, _RunRecord] = {}
        self._active_sessions: dict[tuple[str, str], str] = {}
        self._guard = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(settings.spike_max_concurrency)
        self._checkpointer: Any | None = None
        self._agents = compiled_agents or (self._build_agents(model) if model is not None else {})

    @property
    def available(self) -> bool:
        return bool(self.settings.spike_enabled and self._agents)

    async def create_run(
        self, request: SpikeRunCreateRequest, *, trace_id: str | None = None
    ) -> SpikeRunAccepted:
        if not self.available:
            raise SpikeUnavailableError("DeepAgents Spike requires an enabled model")
        await self._prune_records()
        run_id = f"DAR-{uuid4().hex[:12].upper()}"
        record = _RunRecord(
            run_id=run_id,
            access_token=secrets.token_urlsafe(24),
            session_id=request.session_id,
            user_id=request.user_id,
            scenario=request.scenario,
            objective=request.message,
            language=request.language,
            trace_id=trace_id or uuid4().hex,
        )
        if request.scenario == SpikeScenario.MULTI_PARCEL_RESOLUTION:
            record.allowed_waybills.update(MockSpikeBackend.ORDER_WAYBILLS)
        else:
            record.allowed_case_ids.add(MockSpikeBackend.CUSTOMS_CASE_ID)
            record.facts["budget_limit_vnd"] = 500_000

        session_key = (request.user_id, request.session_id)
        async with self._guard:
            active_count = sum(not item.status.terminal for item in self._records.values())
            if active_count >= self.settings.spike_max_active_runs:
                raise SpikeRunConflictError("DeepAgents Spike has reached its active-run limit")
            if len(self._records) >= self.settings.spike_max_stored_runs:
                raise SpikeRunConflictError("DeepAgents Spike Run Store is full")
            active_id = self._active_sessions.get(session_key)
            if active_id:
                active = self._records.get(active_id)
                if active and not active.status.terminal:
                    raise SpikeRunConflictError("this session already has an active Spike run")
            self._records[run_id] = record
            self._active_sessions[session_key] = run_id

        await self._publish(
            record,
            event_type="run_queued",
            source="runtime",
            title="长任务已进入 DeepAgents Runtime",
            status="pending",
            safe_data={"scenario": request.scenario.value},
        )
        record.task = asyncio.create_task(
            self._execute(record, initial_message=request.message),
            name=f"deepagents-spike-{run_id}",
        )
        return SpikeRunAccepted(
            run_id=run_id,
            access_token=record.access_token,
            status=record.status,
            trace_id=record.trace_id,
        )

    async def get_snapshot(self, run_id: str, access_token: str) -> SpikeRunSnapshot:
        record = await self._authorized_record(run_id, access_token)
        async with record.lock:
            return self._snapshot(record)

    async def resume_run(self, run_id: str, request: SpikeRunResumeRequest) -> SpikeRunSnapshot:
        from langgraph.types import Command

        record = await self._authorized_record(run_id, request.access_token)
        async with record.lock:
            if record.status not in {
                SpikeRunStatus.WAITING_INPUT,
                SpikeRunStatus.WAITING_APPROVAL,
            }:
                raise SpikeRunConflictError("run is not waiting for a decision")
            if request.checkpoint_version != record.checkpoint_version:
                raise SpikeCheckpointConflictError("stale checkpoint version")
            pending = record.pending_action
            if pending is None:
                raise SpikeRunConflictError("run has no pending action")
            if pending.kind == "input" and request.decision != "respond":
                raise SpikeRunConflictError("this checkpoint requires a user response")
            if pending.kind == "approval" and request.decision == "respond":
                raise SpikeRunConflictError("this checkpoint requires approve or reject")

            action_names = [str(item.get("name", "")) for item in pending.actions]
            self._capture_external_decision(record, action_names, request)
            decisions = self._resume_decisions(len(pending.actions), request)
            resume_mode = record.pending_resume_mode
            record.pending_action = None
            record.pending_resume_mode = "command"
            record.status = SpikeRunStatus.QUEUED
            record.reply = None
            record.updated_at = datetime.now(UTC)

        await self._publish(
            record,
            event_type="run_resumed",
            source="human",
            title=self._resume_title(request.decision),
            status="completed",
            safe_data={
                "decision": request.decision,
                "checkpoint_version": request.checkpoint_version,
            },
        )
        command = Command(resume={"decisions": decisions}) if resume_mode == "command" else None
        followup_message = request.message if resume_mode == "message" else None
        async with record.lock:
            if record.status != SpikeRunStatus.CANCELLED:
                record.task = asyncio.create_task(
                    self._execute(
                        record,
                        command=command,
                        followup_message=followup_message,
                    ),
                    name=f"deepagents-spike-resume-{run_id}",
                )
        return await self.get_snapshot(run_id, request.access_token)

    async def cancel_run(self, run_id: str, access_token: str) -> SpikeRunSnapshot:
        record = await self._authorized_record(run_id, access_token)
        async with record.lock:
            if record.status.terminal:
                return self._snapshot(record)
            task = record.task
            record.status = SpikeRunStatus.CANCELLED
            record.pending_action = None
            record.reply = "长任务已取消；未审批的写操作没有执行。"
            record.updated_at = datetime.now(UTC)
        if task and not task.done():
            task.cancel()
        await self._publish(
            record,
            event_type="run_cancelled",
            source="human",
            title="用户取消长任务",
            status="cancelled",
        )
        await self._release_session(record)
        return await self.get_snapshot(run_id, access_token)

    async def shutdown(self) -> None:
        tasks = [
            record.task
            for record in self._records.values()
            if record.task and not record.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(
        self,
        record: _RunRecord,
        *,
        initial_message: str | None = None,
        followup_message: str | None = None,
        command: Any | None = None,
    ) -> None:
        async with record.lock:
            if record.status == SpikeRunStatus.CANCELLED:
                return
            record.status = SpikeRunStatus.RUNNING
            record.updated_at = datetime.now(UTC)
        await self._publish(
            record,
            event_type="agent_started",
            source="agent",
            title="DeepAgent 正在规划并调用专业能力",
            status="running",
        )

        context = SpikeAgentContext(
            run_id=record.run_id,
            scenario=record.scenario.value,
            user_id=record.user_id,
            trace_id=record.trace_id,
            business_tools=self.business_tools,
            spike_backend=self.spike_backend,
            allowed_waybills=record.allowed_waybills,
            allowed_case_ids=record.allowed_case_ids,
            facts=record.facts,
            emit=lambda **event: self._publish(record, **event),
        )
        config = {
            "configurable": {"thread_id": f"spike:{record.run_id}"},
            "recursion_limit": self.settings.spike_recursion_limit,
        }
        invocation: Any = command
        if initial_message is not None:
            invocation = {
                "messages": [
                    {
                        "role": "user",
                        "content": self._initial_instruction(record, initial_message),
                    }
                ]
            }
        elif followup_message is not None:
            invocation = {"messages": [{"role": "user", "content": followup_message}]}

        try:
            async with self._semaphore:
                async with asyncio.timeout(self.settings.spike_task_timeout_seconds):
                    result = await self._agents[record.scenario].ainvoke(
                        invocation,
                        config=config,
                        context=context,
                        version="v2",
                    )
            await self._apply_agent_result(record, result)
        except asyncio.CancelledError:
            if record.status != SpikeRunStatus.CANCELLED:
                await self._fail(record, "任务执行被取消。", "cancelled_during_execution")
            raise
        except TimeoutError:
            await self._fail(record, "长任务执行超时，请重新发起。", "agent_timeout")
        except Exception as exc:
            logger.exception(
                "deepagents_spike_failed run_id=%s trace_id=%s", record.run_id, record.trace_id
            )
            await self._fail(
                record,
                "DeepAgents Spike 执行失败，未确认的写操作没有执行。",
                type(exc).__name__,
            )

    async def _apply_agent_result(self, record: _RunRecord, result: Any) -> None:
        async with record.lock:
            if record.status == SpikeRunStatus.CANCELLED:
                return
        value = getattr(result, "value", result)
        if not isinstance(value, dict):
            value = {}
        await self._publish_agent_capability_events(record, value)
        plan = self._plan_from_value(value)
        if plan:
            async with record.lock:
                record.plan = plan
            await self._publish(
                record,
                event_type="plan_updated",
                source="agent",
                title="Agent Plan 已更新",
                status="completed",
                safe_data={"steps": [item.model_dump() for item in plan]},
            )

        interrupts = list(getattr(result, "interrupts", []) or [])
        if interrupts:
            await self._pause_from_interrupt(record, interrupts[0])
            return

        if await self._handle_completion_guard(record):
            return

        reply = self._clean_reply(self._last_ai_message(value) or "长任务已完成，请查看执行证据。")
        async with record.lock:
            if record.status == SpikeRunStatus.CANCELLED:
                return
            record.status = SpikeRunStatus.COMPLETED
            record.reply = reply
            record.pending_action = None
            record.result = self._result_summary(record)
            record.updated_at = datetime.now(UTC)
        await self._publish(
            record,
            event_type="run_completed",
            source="runtime",
            title="DeepAgents 长任务完成",
            status="completed",
            safe_data=record.result,
        )
        await self._release_session(record)

    async def _pause_from_interrupt(self, record: _RunRecord, interrupt_item: Any) -> None:
        raw = getattr(interrupt_item, "value", interrupt_item)
        raw = raw if isinstance(raw, dict) else {}
        action_requests = raw.get("action_requests", [])
        review_configs = raw.get("review_configs", [])
        actions = [
            {
                "name": str(item.get("name", "unknown")),
                "args": self._approval_args(item.get("args", {})),
                "description": "Agent action requires user or human review.",
            }
            for item in action_requests
            if isinstance(item, dict)
        ]
        if not actions:
            actions = [{"name": "unknown", "args": {}, "description": "Agent 请求人工处理"}]
        action_names = {str(item.get("name", "")) for item in actions}
        if action_names & INPUT_ACTIONS and not action_names.issubset(INPUT_ACTIONS):
            await self._fail(
                record,
                "Agent 将补充信息和写操作放进了同一审批批次；为避免错误授权，本次任务已安全停止。",
                "mixed_interrupt_batch",
            )
            return
        kind = "input" if action_names.issubset(INPUT_ACTIONS) else "approval"
        allowed = sorted(
            {
                str(decision)
                for item in review_configs
                if isinstance(item, dict)
                for decision in item.get("allowed_decisions", [])
            }
        )
        prompt = self._pending_prompt(kind, actions)
        pending = SpikePendingAction(
            kind=kind,
            actions=actions,
            allowed_decisions=allowed,
            prompt=prompt,
        )
        async with record.lock:
            if record.status == SpikeRunStatus.CANCELLED:
                return
            record.checkpoint_version += 1
            record.pending_action = pending
            record.pending_resume_mode = "command"
            record.status = (
                SpikeRunStatus.WAITING_INPUT if kind == "input" else SpikeRunStatus.WAITING_APPROVAL
            )
            record.reply = prompt
            record.updated_at = datetime.now(UTC)
            version = record.checkpoint_version
        await self._publish(
            record,
            event_type="run_paused",
            source="human",
            title="等待补充信息" if kind == "input" else "等待写操作确认",
            status="paused",
            safe_data={"checkpoint_version": version, "actions": actions},
        )

    async def _handle_completion_guard(self, record: _RunRecord) -> bool:
        guard = self._completion_guard(record)
        if guard is None:
            return False
        if guard["kind"] == "pause":
            await self._synthetic_input_pause(
                record,
                action_name=guard["action_name"],
                prompt=guard["prompt"],
                requested_fields=guard.get("requested_fields", []),
            )
            return True

        stage = guard["stage"]
        retries = record.facts.setdefault("runtime_guard_retries", {})
        retry_count = int(retries.get(stage, 0)) + 1
        retries[stage] = retry_count
        if retry_count > 2:
            await self._fail(
                record,
                "Agent 未能完成必需的安全步骤；本次任务已停止，未审批的写操作没有执行。",
                f"completion_guard_exhausted:{stage}",
            )
            return True
        await self._publish(
            record,
            event_type="runtime_guard",
            source="runtime",
            title="Runtime 检测到未完成步骤，要求 Agent 继续执行",
            status="running",
            safe_data={"stage": stage, "retry": retry_count},
        )
        await self._execute(record, followup_message=guard["message"])
        return True

    async def _synthetic_input_pause(
        self,
        record: _RunRecord,
        *,
        action_name: str,
        prompt: str,
        requested_fields: list[str],
    ) -> None:
        clean_prompt = self._clean_reply(prompt)
        action = {
            "name": action_name,
            "args": {"requested_fields": requested_fields},
            "description": "Runtime requires external information before this task can finish.",
        }
        pending = SpikePendingAction(
            kind="input",
            actions=[action],
            allowed_decisions=["respond"],
            prompt=clean_prompt,
        )
        async with record.lock:
            if record.status == SpikeRunStatus.CANCELLED:
                return
            record.checkpoint_version += 1
            record.pending_action = pending
            record.pending_resume_mode = "message"
            record.status = SpikeRunStatus.WAITING_INPUT
            record.reply = clean_prompt
            record.updated_at = datetime.now(UTC)
            version = record.checkpoint_version
        await self._publish(
            record,
            event_type="run_paused",
            source="runtime",
            title="Runtime 阻止任务提前结束，等待外部信息",
            status="paused",
            safe_data={"checkpoint_version": version, "actions": [action]},
        )

    @staticmethod
    def _completion_guard(record: _RunRecord) -> dict[str, Any] | None:
        if record.scenario == SpikeScenario.MULTI_PARCEL_RESOLUTION:
            return DeepAgentsSpikeService._order_completion_guard(record)

        facts = record.facts
        document_ids = facts.get("customs_document_ids")
        bundle_complete = facts.get("customs_bundle_complete") is True
        no_valid_report = facts.get("no_valid_battery_report") is True
        rejected_actions = set(facts.get("rejected_actions", []))

        if not document_ids or (not bundle_complete and not no_valid_report):
            return {
                "kind": "pause",
                "action_name": "request_information",
                "prompt": (
                    "请补充有效电池测试报告和液体成分说明。测试可回复 "
                    "DOC-BAT-VALID、DOC-LIQ-VALID；如果没有其他有效电池报告，也请明确说明，"
                    "我会按原条件改为退回方案。"
                ),
                "requested_fields": ["battery_report", "ingredient_statement"],
            }

        if not bundle_complete and no_valid_report:
            if facts.get("return_request_id") or "request_return_to_sender" in rejected_actions:
                return None
            return {
                "kind": "retry",
                "stage": "return_after_invalid_documents",
                "message": (
                    "Runtime guard: the inspected bundle is incomplete and the user explicitly has "
                    "no valid replacement. Follow the original conditional objective now: call "
                    "request_return_to_sender for CB-VN-CN-001 so the runtime can request final "
                    "approval. Do not ask for the same document again and do not finish in prose."
                ),
            }

        quote = facts.get("customs_quote")
        if not isinstance(quote, dict):
            return {
                "kind": "retry",
                "stage": "quote_required",
                "message": (
                    "Runtime guard: the valid document bundle has not been compared with the user's "
                    "budget. Call quote_customs_options and continue the Skill workflow."
                ),
            }
        supplement = quote.get("supplement_documents", {})
        amount = supplement.get("amount") if isinstance(supplement, dict) else None
        budget = int(facts.get("budget_limit_vnd", 0))
        if not isinstance(amount, int) or amount > budget:
            if facts.get("return_request_id") or "request_return_to_sender" in rejected_actions:
                return None
            return {
                "kind": "retry",
                "stage": "return_over_budget",
                "message": (
                    "Runtime guard: document supplementation is outside the user's budget. Call "
                    "request_return_to_sender so the runtime can request explicit final approval."
                ),
            }

        review_id = facts.get("compliance_review_id")
        if not review_id:
            return {
                "kind": "retry",
                "stage": "compliance_review_required",
                "message": (
                    "Runtime guard: the complete, within-budget bundle still requires simulated "
                    "compliance review. Call submit_compliance_review with the inspected documents, "
                    "then call request_compliance_decision."
                ),
            }
        if "compliance_approved" not in facts:
            return {
                "kind": "pause",
                "action_name": "request_compliance_decision",
                "prompt": (
                    f"材料包已进入模拟合规复核（{review_id}）。请明确回复“合规批准”或“合规拒绝”。"
                ),
                "requested_fields": ["compliance_decision"],
            }
        if facts.get("compliance_approved") is True:
            if facts.get("customs_submission_id") or "submit_customs_documents" in rejected_actions:
                return None
            return {
                "kind": "retry",
                "stage": "customs_submission_required",
                "message": (
                    "Runtime guard: compliance approved the exact document bundle, but no customs "
                    "submission receipt exists. Call submit_customs_documents with that bundle so "
                    "the runtime can request final user approval."
                ),
            }
        if facts.get("return_request_id") or "request_return_to_sender" in rejected_actions:
            return None
        return {
            "kind": "retry",
            "stage": "return_after_compliance_rejection",
            "message": (
                "Runtime guard: compliance rejected the document route. Replan to "
                "request_return_to_sender so the runtime can request final user approval."
            ),
        }

    @staticmethod
    def _order_completion_guard(record: _RunRecord) -> dict[str, Any] | None:
        facts = record.facts
        if not facts.get("user_supplied_new_address") or not facts.get("user_supplied_phone_last4"):
            return {
                "kind": "pause",
                "action_name": "request_information",
                "prompt": (
                    "请一次性补充新收件地址和收件手机号后四位。测试可回复："
                    "新地址是 123 Nguyen Hue Street, District 1，手机号后四位 1234。"
                ),
                "requested_fields": ["new_address", "phone_last4"],
            }

        if "JT100000005" not in facts.get("verified_waybills", set()):
            return {
                "kind": "retry",
                "stage": "receiver_verification_required",
                "message": (
                    "Runtime guard: receiver verification is still missing. Call verify_receiver "
                    "for JT100000005 using the phone last four digits supplied in the latest "
                    "external response, then continue the original order objective."
                ),
            }

        required_actions = {
            "submit_address_change": "address_change_request_id",
            "submit_delivery_followup": "delivery_followup_ticket_id",
            "submit_missing_delivery_case": "missing_delivery_ticket_id",
            "submit_outlet_hold": "outlet_hold_request_id",
        }
        rejected_actions = set(facts.get("rejected_actions", []))
        unresolved = [
            action
            for action, receipt_key in required_actions.items()
            if not facts.get(receipt_key) and action not in rejected_actions
        ]
        if not unresolved:
            return None
        return {
            "kind": "retry",
            "stage": "order_write_actions_required",
            "message": (
                "Runtime guard: the user's order-level objective is not complete. Call every "
                "still-unresolved write Tool in one response so Human-in-the-loop can show a single "
                f"approval batch. Required unresolved Tools: {', '.join(unresolved)}. Use the exact "
                "user-supplied address and the verified delivered shipment. Do not finish in prose."
            ),
        }

    async def _fail(self, record: _RunRecord, reply: str, reason: str) -> None:
        async with record.lock:
            if record.status == SpikeRunStatus.CANCELLED:
                return
            record.status = SpikeRunStatus.FAILED
            record.reply = reply
            record.pending_action = None
            record.result = {"failure_reason": reason}
            record.updated_at = datetime.now(UTC)
        await self._publish(
            record,
            event_type="run_failed",
            source="runtime",
            title="DeepAgents 长任务失败",
            status="failed",
            safe_data={"reason": reason},
        )
        await self._release_session(record)

    async def _publish(
        self,
        record: _RunRecord,
        *,
        event_type: str,
        source: str,
        title: str,
        status: str,
        safe_data: dict[str, Any] | None = None,
    ) -> None:
        async with record.lock:
            event = SpikeEvent(
                sequence=record.next_event_sequence,
                event_type=event_type,
                source=source,
                title=title,
                status=status,
                occurred_at=datetime.now(UTC),
                safe_data=self._safe(safe_data or {}),
            )
            record.next_event_sequence += 1
            record.events.append(event)
            record.events = record.events[-200:]
            if event_type == "tool_completed":
                record.tool_evidence_count += 1
                output = event.safe_data.get("output", {})
                if isinstance(output, dict):
                    for key in (
                        "ticket_id",
                        "request_id",
                        "hold_request_id",
                        "submission_id",
                        "return_request_id",
                        "review_id",
                    ):
                        reference = output.get(key)
                        if reference and str(reference) not in record.reference_ids:
                            record.reference_ids.append(str(reference))
            record.updated_at = datetime.now(UTC)

    async def _authorized_record(self, run_id: str, access_token: str) -> _RunRecord:
        async with self._guard:
            record = self._records.get(run_id)
        if record is None or not hmac.compare_digest(record.access_token, access_token):
            raise SpikeRunNotFoundError("run not found")
        return record

    async def _release_session(self, record: _RunRecord) -> None:
        session_key = (record.user_id, record.session_id)
        async with self._guard:
            if self._active_sessions.get(session_key) == record.run_id:
                self._active_sessions.pop(session_key, None)

    async def _prune_records(self) -> None:
        now = datetime.now(UTC)
        removed: list[str] = []
        async with self._guard:
            terminal = sorted(
                (item for item in self._records.values() if item.status.terminal),
                key=lambda item: item.updated_at,
            )
            for record in terminal:
                expired = (
                    now - record.updated_at
                ).total_seconds() >= self.settings.spike_run_ttl_seconds
                store_full = len(self._records) >= self.settings.spike_max_stored_runs
                if not expired and not store_full:
                    continue
                self._records.pop(record.run_id, None)
                removed.append(record.run_id)
        if self._checkpointer is not None:
            for run_id in removed:
                try:
                    await self._checkpointer.adelete_thread(f"spike:{run_id}")
                except Exception:
                    logger.warning("failed_to_prune_spike_checkpoint run_id=%s", run_id)

    @staticmethod
    def _snapshot(record: _RunRecord) -> SpikeRunSnapshot:
        return SpikeRunSnapshot(
            run_id=record.run_id,
            scenario=record.scenario,
            scenario_title=SCENARIO_TITLES[record.scenario],
            objective=record.objective,
            status=record.status,
            reply=record.reply,
            plan=[item.model_copy(deep=True) for item in record.plan],
            events=[item.model_copy(deep=True) for item in record.events],
            pending_action=(
                record.pending_action.model_copy(deep=True) if record.pending_action else None
            ),
            checkpoint_version=record.checkpoint_version,
            result=dict(record.result),
            trace_id=record.trace_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _resume_decisions(count: int, request: SpikeRunResumeRequest) -> list[dict[str, Any]]:
        if request.decision == "respond":
            return [{"type": "respond", "message": request.message or ""} for _ in range(count)]
        if request.decision == "reject":
            return [
                {
                    "type": "reject",
                    "message": request.message
                    or "用户拒绝本次写操作。请保留已完成调查并重新规划。",
                }
                for _ in range(count)
            ]
        return [{"type": "approve"} for _ in range(count)]

    @staticmethod
    def _capture_external_decision(
        record: _RunRecord, action_names: list[str], request: SpikeRunResumeRequest
    ) -> None:
        if request.decision == "reject":
            rejected = set(record.facts.get("rejected_actions", []))
            rejected.update(action_names)
            record.facts["rejected_actions"] = sorted(rejected)
            return
        if request.decision != "respond":
            return
        message = (request.message or "").lower()
        raw_message = request.message or ""
        record.facts["last_user_response"] = request.message or ""
        if "request_information" in action_names:
            phone_match = re.search(
                r"(?:手机号|手机|phone)(?:\s*(?:后四位|last\s*(?:4|four)))?\D{0,12}(\d{4})(?!\d)",
                raw_message,
                flags=re.IGNORECASE,
            )
            if phone_match:
                record.facts["user_supplied_phone_last4"] = phone_match.group(1)
            address_match = re.search(
                r"(?:新地址|new\s+address|address)(?:是|为|\s+is|[:：])?\s*(.+?)"
                r"(?=[，,;；]\s*(?:手机号|手机|phone)|$)",
                raw_message,
                flags=re.IGNORECASE,
            )
            if address_match:
                record.facts["user_supplied_new_address"] = address_match.group(1).strip()
            document_ids = sorted(set(re.findall(r"DOC-[A-Z0-9-]+", raw_message.upper())))
            if document_ids:
                record.facts["user_supplied_document_ids"] = document_ids
            no_valid_tokens = (
                "没有有效",
                "没有其他有效",
                "no valid",
                "cannot provide a valid",
            )
            if any(token in message for token in no_valid_tokens):
                record.facts["no_valid_battery_report"] = True
            if "doc-bat-valid" in message:
                record.facts["no_valid_battery_report"] = False
        if "request_compliance_decision" not in action_names:
            return
        rejected = any(
            token in message for token in ("拒绝", "不通过", "不批准", "reject", "rejected")
        )
        approved = not rejected and any(
            token in message for token in ("批准", "通过", "approve", "approved")
        )
        if approved == rejected:
            raise SpikeRunConflictError("compliance response must clearly approve or reject")
        record.facts["compliance_approved"] = approved
        record.facts["decided_compliance_review_id"] = record.facts.get("compliance_review_id")
        record.facts["decided_document_ids"] = list(record.facts.get("reviewed_document_ids", []))

    @staticmethod
    def _resume_title(decision: str) -> str:
        return {
            "respond": "用户/人工已补充信息，恢复原任务",
            "approve": "用户批准待执行动作，恢复原任务",
            "reject": "用户拒绝待执行动作，要求 Agent 重新规划",
        }[decision]

    @staticmethod
    def _plan_from_value(value: dict[str, Any]) -> list[SpikePlanItem]:
        result: list[SpikePlanItem] = []
        mapping = {"in_progress": "in_progress", "pending": "pending", "completed": "completed"}
        for item in value.get("todos", []) or []:
            if not isinstance(item, dict) or not item.get("content"):
                continue
            status = mapping.get(str(item.get("status", "pending")), "pending")
            result.append(SpikePlanItem(content=str(item["content"])[:500], status=status))
        return result

    @staticmethod
    def _last_ai_message(value: dict[str, Any]) -> str | None:
        for message in reversed(value.get("messages", []) or []):
            if getattr(message, "type", None) != "ai":
                continue
            content = getattr(message, "content", "")
            if isinstance(content, str) and content.strip():
                return content.strip().replace("**", "")
        return None

    @staticmethod
    def _pending_prompt(kind: str, actions: list[dict[str, Any]]) -> str:
        if kind == "input":
            first = actions[0]
            args = first.get("args", {})
            if first.get("name") == "request_information":
                return DeepAgentsSpikeService._clean_reply(
                    str(args.get("question") or "请补充继续调查所需的信息。")
                )
            return "材料已完成系统检查。请模拟合规人员明确回复“批准”或“拒绝”。"
        action_lines = [f"- {item.get('name')}：{item.get('args', {})}" for item in actions]
        return "调查和规划已经完成。以下写操作尚未执行，请确认或拒绝：\n" + "\n".join(action_lines)

    @staticmethod
    def _result_summary(record: _RunRecord) -> dict[str, Any]:
        return {
            "evidence_count": record.tool_evidence_count,
            "reference_ids": list(record.reference_ids),
            "runtime": "DeepAgents + LangGraph checkpoint (process-local Spike)",
        }

    @staticmethod
    def _safe(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, child in value.items():
                if key in {"phone_last4", "idempotency_key", "access_token"}:
                    result[key] = "****"
                elif key in {"new_address", "address"} and isinstance(child, str):
                    result[key] = child[:12] + "…" if len(child) > 12 else child
                else:
                    result[key] = DeepAgentsSpikeService._safe(child)
            return result
        if isinstance(value, (list, tuple, set)):
            return [DeepAgentsSpikeService._safe(item) for item in value]
        return value

    @staticmethod
    def _approval_args(value: Any) -> Any:
        """Keep user-visible action details intact while masking credentials and internal keys."""
        if isinstance(value, dict):
            return {
                key: (
                    "****"
                    if key in {"phone_last4", "idempotency_key", "access_token"}
                    else DeepAgentsSpikeService._approval_args(child)
                )
                for key, child in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [DeepAgentsSpikeService._approval_args(item) for item in value]
        return value

    async def _publish_agent_capability_events(
        self, record: _RunRecord, value: dict[str, Any]
    ) -> None:
        observed = set(record.facts.get("observed_agent_tool_calls", []))
        for message in value.get("messages", []) or []:
            for call in getattr(message, "tool_calls", []) or []:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name", ""))
                args = call.get("args", {})
                args = args if isinstance(args, dict) else {}
                marker = str(call.get("id") or f"{name}:{args}")
                if marker in observed:
                    continue
                if name == "read_file" and "/skills/" in str(args.get("file_path", "")):
                    path = str(args.get("file_path", ""))
                    await self._publish(
                        record,
                        event_type="skill_loaded",
                        source="skill",
                        title=f"按需加载 Skill：{Path(path).parent.name}",
                        status="completed",
                        safe_data={"path": path},
                    )
                    observed.add(marker)
                elif name == "task":
                    subagent_type = str(args.get("subagent_type", "specialist"))
                    await self._publish(
                        record,
                        event_type="subagent_delegated",
                        source="subagent",
                        title=f"委派专业子 Agent：{subagent_type}",
                        status="completed",
                        safe_data={"subagent_type": subagent_type},
                    )
                    observed.add(marker)
        record.facts["observed_agent_tool_calls"] = sorted(observed)

    @staticmethod
    def _clean_reply(reply: str) -> str:
        lines: list[str] = []
        for raw_line in reply.replace("**", "").replace("`", "").splitlines():
            stripped = raw_line.strip()
            if stripped in {"---", "___", "***"}:
                continue
            lines.append(raw_line.lstrip("# "))
        return "\n".join(lines).strip()

    @staticmethod
    def _initial_instruction(record: _RunRecord, message: str) -> str:
        return (
            f"Run the DeepAgents Spike scenario `{record.scenario.value}`. "
            "This is a multi-turn long task. Read the matching Skill, create a Todo plan, "
            "delegate evidence gathering to the named specialist subagent, and use only Tool facts. "
            "Use request_information for missing user data; do not ask only in prose. "
            "All write Tools are paused by runtime approval. Reply in Chinese.\n\n"
            f"User objective:\n{message}"
        )

    def _build_agents(self, model: Any) -> dict[SpikeScenario, Any]:
        from deepagents import FilesystemPermission, create_deep_agent
        from deepagents.backends import StoreBackend
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()
        backend = StoreBackend(store=store, namespace=lambda _runtime: ("deepagents-spike",))
        files = [
            (f"/skills/{path.relative_to(SPIKE_SKILLS_ROOT).as_posix()}", path.read_bytes())
            for path in SPIKE_SKILLS_ROOT.rglob("*")
            if path.is_file()
        ]
        uploaded = backend.upload_files(files)
        if any(item.error for item in uploaded):
            raise RuntimeError("failed to seed DeepAgents Spike Skills")
        checkpointer = InMemorySaver()
        self._checkpointer = checkpointer
        permissions = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
        common_prompt = (
            "You are a bounded logistics investigation orchestrator for a framework Spike. "
            "You must use write_todos, progressively read the relevant SKILL.md, and delegate "
            "evidence collection with the task tool to the provided specialist. Never reveal hidden "
            "reasoning; only expose the auditable plan, Tool evidence, decisions, and final result. "
            "Never invent business facts or reference IDs. Never use shell commands or write files. "
            "Never claim a write succeeded until its Tool returns after approval. Once a write Tool "
            "returns successfully, describe it as executed rather than still waiting for approval. "
            "Final user replies must be concise Chinese plain text: no Markdown tables, headings, "
            "backticks, bold markers, or decorative emoji."
        )
        disabled_general_subagent = {
            "name": "general-purpose",
            "description": (
                "Disabled safety placeholder. Never select this agent; use the named scenario "
                "specialist instead."
            ),
            "system_prompt": "Return that this subagent is disabled without calling any tools.",
            "tools": [],
            "permissions": permissions,
        }
        order_agent = create_deep_agent(
            model=model,
            tools=[request_information, verify_receiver, *ORDER_WRITE_TOOLS],
            system_prompt=common_prompt
            + " Handle only order ORD-DA-001 and its allowlisted shipments for this scenario. "
            "You do not own bulk investigation Tools: delegate that work exactly once to "
            "multi-parcel-operations-analyst, then use its evidence without repeating the reads.",
            subagents=[
                disabled_general_subagent,
                {
                    "name": "multi-parcel-operations-analyst",
                    "description": "Investigates every shipment in a split order and returns per-waybill evidence and safe candidate actions.",
                    "system_prompt": (
                        "Investigate every supplied waybill using the available read-only Tools. "
                        "Check live tracking and existing cases for each, then the relevant address, "
                        "POD, or outlet-hold evidence. Return a concise per-waybill evidence report "
                        "with evidence IDs. Never propose facts not returned by a Tool."
                    ),
                    "tools": ORDER_READ_TOOLS[:-1],
                    "permissions": permissions,
                },
            ],
            skills=["/skills/"],
            permissions=permissions,
            backend=backend,
            store=store,
            context_schema=SpikeAgentContext,
            interrupt_on={
                "request_information": {"allowed_decisions": ["respond"]},
                **{
                    tool.name: {"allowed_decisions": ["approve", "reject"]}
                    for tool in ORDER_WRITE_TOOLS
                },
            },
            checkpointer=checkpointer,
            name="multi-parcel-resolution-spike",
        )
        customs_agent = create_deep_agent(
            model=model,
            tools=[
                inspect_document_bundle,
                quote_customs_options,
                submit_compliance_review,
                request_information,
                request_compliance_decision,
                *CUSTOMS_WRITE_TOOLS,
            ],
            system_prompt=common_prompt
            + " Handle only customs case CB-VN-CN-001 and enforce the 500000 VND user budget. "
            "Delegate initial case, declaration, and policy investigation exactly once to "
            "customs-compliance-analyst and do not repeat those reads in the main agent. "
            "If the user has no valid replacement for an expired required document, do not finish "
            "with another prose question: follow the user's conditional objective and propose the "
            "approval-gated return_to_sender Tool.",
            subagents=[
                disabled_general_subagent,
                {
                    "name": "customs-compliance-analyst",
                    "description": "Investigates declaration, route policy, documents, and resolution costs without making the final compliance decision.",
                    "system_prompt": (
                        "Use read-only Tools to examine the customs case, declaration, versioned route "
                        "policy, current documents, and resolution quote. Return missing documents, "
                        "policy IDs, costs, and evidence IDs. This is Mock policy, not legal advice."
                    ),
                    "tools": CUSTOMS_READ_TOOLS[:3],
                    "permissions": permissions,
                },
            ],
            skills=["/skills/"],
            permissions=permissions,
            backend=backend,
            store=store,
            context_schema=SpikeAgentContext,
            interrupt_on={
                "request_information": {"allowed_decisions": ["respond"]},
                "request_compliance_decision": {"allowed_decisions": ["respond"]},
                **{
                    tool.name: {"allowed_decisions": ["approve", "reject"]}
                    for tool in CUSTOMS_WRITE_TOOLS
                },
            },
            checkpointer=checkpointer,
            name="crossborder-customs-spike",
        )
        return {
            SpikeScenario.MULTI_PARCEL_RESOLUTION: order_agent,
            SpikeScenario.CROSSBORDER_CUSTOMS: customs_agent,
        }
