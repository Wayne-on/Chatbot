from __future__ import annotations

import ast
import json
import re
from collections import deque
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - developer utility
    raise SystemExit("缺少 PyYAML，请先安装后再运行 DSL 校验。") from exc


ROOT = Path(__file__).resolve().parent
REFERENCE_PATTERN = re.compile(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)#\}\}")


def load_target() -> tuple[Path, dict[str, Any]]:
    path = ROOT / "中国物流智能客服-Demo.yml"
    assert path.exists(), "未找到中国物流智能客服主 DSL。"
    return path, yaml.safe_load(path.read_text(encoding="utf-8"))


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def available_outputs(node: dict[str, Any]) -> set[str]:
    data = node["data"]
    node_type = data["type"]
    if node_type == "start":
        return {item["variable"] for item in data.get("variables", [])}
    if node_type == "llm":
        return {"text"}
    if node_type == "variable-aggregator":
        return {"output"}
    outputs = data.get("outputs", {})
    return set(outputs) if isinstance(outputs, dict) else set()


def validate_references(workflow: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> None:
    conversation = {item["name"] for item in workflow.get("conversation_variables", [])}

    def check(source: str, variable: str) -> None:
        if source == "sys":
            assert variable in {"query", "conversation_id"}, f"未知系统变量：{source}.{variable}"
        elif source == "conversation":
            assert variable in conversation, f"未知会话变量：{source}.{variable}"
        else:
            assert source in nodes, f"引用了不存在的节点：{source}.{variable}"
            assert variable in available_outputs(nodes[source]), f"未知节点输出：{source}.{variable}"

    for node in nodes.values():
        for item in walk(node["data"]):
            for key in ("value_selector", "variable_selector"):
                selector = item.get(key)
                if isinstance(selector, list) and len(selector) == 2:
                    check(str(selector[0]), str(selector[1]))
            value = item.get("value")
            if isinstance(value, list) and len(value) == 2:
                check(str(value[0]), str(value[1]))

        serialized = json.dumps(node["data"], ensure_ascii=False)
        for source, variable in REFERENCE_PATTERN.findall(serialized):
            check(source, variable)


def validate_graph(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    graph = workflow["graph"]
    node_list = graph["nodes"]
    nodes = {node["id"]: node for node in node_list}
    assert len(nodes) == len(node_list), "存在重复节点 ID。"
    assert "start" in nodes, "缺少 start 节点。"

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    edge_ids: set[str] = set()
    for edge in graph["edges"]:
        assert edge["id"] not in edge_ids, f"存在重复边 ID：{edge['id']}"
        edge_ids.add(edge["id"])
        source = edge["source"]
        target = edge["target"]
        assert source in nodes, f"边引用了不存在的源节点：{source}"
        assert target in nodes, f"边引用了不存在的目标节点：{target}"
        adjacency[source].append(target)

        if nodes[source]["data"]["type"] == "if-else":
            valid_handles = {case["case_id"] for case in nodes[source]["data"].get("cases", [])}
            valid_handles.add("false")
            assert edge["sourceHandle"] in valid_handles, (
                f"条件边 {edge['id']} 使用了不存在的分支：{edge['sourceHandle']}"
            )

    visited: set[str] = set()
    queue = deque(["start"])
    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        queue.extend(adjacency[node_id])
    unreachable = set(nodes) - visited
    assert not unreachable, f"存在不可达节点：{sorted(unreachable)}"

    for node_id, node in nodes.items():
        code = node["data"].get("code")
        if isinstance(code, str):
            ast.parse(code, filename=f"{node_id}.py")
    return nodes


def validate_state_and_routing_contract(
    workflow: dict[str, Any], nodes: dict[str, dict[str, Any]]
) -> None:
    variables = workflow.get("conversation_variables", [])
    by_name = {item["name"]: item for item in variables}
    assert len(by_name) == len(variables), "存在重名会话变量。"
    required = {
        "scenario",
        "level3_intent",
        "dialogue_act",
        "intent_relation",
        "pending_tasks_json",
        "waybill_no",
        "phone",
        "pending_action",
        "current_step",
    }
    assert required <= set(by_name), f"缺少会话 State：{sorted(required - set(by_name))}"
    assert json.loads(by_name["pending_tasks_json"]["value"]) == []

    for node_id in (
        "code_fast_path",
        "if_need_llm",
        "llm_understand",
        "aggregate_semantic_result",
        "code_parse",
        "assign_context_memory",
    ):
        assert node_id in nodes, f"缺少受控路由节点：{node_id}"

    edges = workflow["graph"]["edges"]
    triples = {(edge["source"], edge["sourceHandle"], edge["target"]) for edge in edges}
    assert ("start", "source", "code_fast_path") in triples
    assert ("code_fast_path", "source", "if_need_llm") in triples
    assert ("if_need_llm", "true", "llm_understand") in triples
    assert ("if_need_llm", "false", "aggregate_semantic_result") in triples
    assert ("llm_understand", "source", "aggregate_semantic_result") in triples
    assert ("aggregate_semantic_result", "source", "code_parse") in triples
    assert not any(edge["source"] == "start" and edge["target"] == "llm_understand" for edge in edges)

    prompt_text = json.dumps(nodes["llm_understand"]["data"]["prompt_template"], ensure_ascii=False)
    for key in ("intents", "level2", "level3", "dialogue_act", "intent_relation", "polarity", "slots"):
        assert key in prompt_text, f"分层语义 Prompt 缺少字段：{key}"

    parser_outputs = available_outputs(nodes["code_parse"])
    for output in (
        "level2_intent",
        "level3_intent",
        "dialogue_act",
        "intent_relation",
        "pending_tasks_json",
        "pending_task_hint",
        "pending_task_ready_hint",
        "task_type",
    ):
        assert output in parser_outputs, f"code_parse 缺少输出：{output}"

    assigned = {
        item["variable_selector"][1]: tuple(item["value"])
        for item in nodes["assign_context_memory"]["data"]["items"]
    }
    for field in ("scenario", "level3_intent", "dialogue_act", "intent_relation", "pending_tasks_json"):
        assert assigned.get(field) == ("code_parse", field), f"会话 State 未正确写回：{field}"

    openapi_path = ROOT / "场景" / "催查件" / "催查件MVP-业务Tool.openapi.yaml"
    openapi = yaml.safe_load(openapi_path.read_text(encoding="utf-8"))
    expected_statuses = set(
        openapi["components"]["schemas"]["SnapshotResponse"]["properties"]["statusCode"]["enum"]
    )
    actual_statuses = {
        case["conditions"][0]["value"]
        for case in nodes["if_expedite_status"]["data"]["cases"]
    }
    assert actual_statuses == expected_statuses, "催查件 Tool 枚举与 DSL 分支不一致。"
    fallback_edges = [
        edge for edge in edges
        if edge["source"] == "if_expedite_status" and edge["sourceHandle"] == "false"
    ]
    assert len(fallback_edges) == 1 and fallback_edges[0]["target"] == "answer_expedite_system_error"

    expedite_assignments = {
        item["variable_selector"][1]: tuple(item["value"])
        for item in nodes["assign_expedite_query_memory"]["data"]["items"]
    }
    assert expedite_assignments.get("waybill_no") == (
        "code_expedite_query_tool", "resolved_waybill_no"
    ), "无效运单查询后没有可靠的 State 清理路径。"


def smoke_test(nodes: dict[str, dict[str, Any]]) -> None:
    fast_namespace: dict[str, Any] = {}
    exec(nodes["code_fast_path"]["data"]["code"], fast_namespace)
    fast_path = fast_namespace["main"]

    parser_namespace: dict[str, Any] = {}
    exec(nodes["code_parse"]["data"]["code"], parser_namespace)
    parse = parser_namespace["main"]

    tool_namespace: dict[str, Any] = {}
    exec(nodes["code_mock_tools"]["data"]["code"], tool_namespace)
    run_tool = tool_namespace["main"]

    expedite_namespace: dict[str, Any] = {}
    exec(nodes["code_expedite_query_tool"]["data"]["code"], expedite_namespace)
    query_expedite = expedite_namespace["main"]

    work_order_namespace: dict[str, Any] = {}
    exec(nodes["code_expedite_work_order_tool"]["data"]["code"], work_order_namespace)
    create_expedite_work_order = work_order_namespace["main"]

    class ConversationHarness:
        def __init__(self, **initial: str) -> None:
            self.state: dict[str, str] = {
                "scenario": "",
                "level3_intent": "",
                "dialogue_act": "unknown",
                "intent_relation": "single",
                "pending_tasks_json": "[]",
                "waybill_no": "",
                "phone": "",
                "pending_action": "",
                "current_step": "idle",
                "last_business_reason": "",
                "problem_description": "",
                "last_tool_summary": "",
            }
            self.state.update(initial)
            self.visited_nodes: list[str] = []
            self.called_tools: list[str] = []
            self.last_tool_result: dict[str, Any] | None = None

        def turn(self, query: str, llm: dict[str, Any] | None = None) -> dict[str, Any]:
            self.visited_nodes = ["start", "code_fast_path"]
            self.called_tools = []
            self.last_tool_result = None
            fast = fast_path(
                query=query,
                prev_scenario=self.state["scenario"],
                prev_level3_intent=self.state["level3_intent"],
                prev_current_step=self.state["current_step"],
                prev_pending_action=self.state["pending_action"],
                prev_pending_tasks_json=self.state["pending_tasks_json"],
            )
            self.visited_nodes.append("if_need_llm")
            if fast["use_llm"] == "true":
                assert llm is not None, f"用例需要提供模型语义输出：{query}"
                self.visited_nodes.append("llm_understand")
                semantic_text = json.dumps(llm, ensure_ascii=False)
            else:
                semantic_text = fast["semantic_json"]
            self.visited_nodes.extend(["aggregate_semantic_result", "code_parse", "assign_context_memory"])
            result = parse(
                semantic_text=semantic_text,
                query=query,
                prev_waybill=self.state["waybill_no"],
                prev_phone=self.state["phone"],
                prev_scenario=self.state["scenario"],
                prev_level3_intent=self.state["level3_intent"],
                prev_pending_action=self.state["pending_action"],
                prev_current_step=self.state["current_step"],
                prev_business_reason=self.state["last_business_reason"],
                prev_problem_description=self.state["problem_description"],
                prev_pending_tasks_json=self.state["pending_tasks_json"],
            )
            for field in (
                "scenario", "level3_intent", "dialogue_act", "intent_relation",
                "pending_tasks_json", "waybill_no", "phone", "pending_action",
                "current_step", "problem_description",
            ):
                self.state[field] = str(result[field])
            self.state["last_business_reason"] = str(result["business_reason"])

            tool_name = result["tool_name"]
            if result["direct_reply"] == "false" and tool_name not in {"", "none"}:
                self.called_tools.append(tool_name)
                if tool_name == "query_expedite_snapshot":
                    tool_result = query_expedite(
                        result["waybill_no"], result["level3_intent"], query,
                        result["pending_task_hint"], result["pending_task_ready_hint"],
                    )
                    self.state["waybill_no"] = tool_result["resolved_waybill_no"]
                elif tool_name == "create_expedite_work_order":
                    tool_result = create_expedite_work_order(
                        result["waybill_no"], result["phone"],
                        result["problem_description"], "validator-conversation",
                    )
                else:
                    tool_result = run_tool(
                        result["waybill_no"], result["phone"], result["scenario"],
                        tool_name, query, result["business_reason"],
                    )
                self.last_tool_result = tool_result
                self.state["current_step"] = str(tool_result["next_step"])
                self.state["last_tool_summary"] = str(tool_result["tool_summary"])
            return result

    def route(llm: dict[str, Any], query: str, **state: str) -> dict[str, Any]:
        return parse(
            semantic_text=json.dumps(llm, ensure_ascii=False),
            query=query,
            prev_waybill=state.get("waybill_no", ""),
            prev_phone=state.get("phone", ""),
            prev_scenario=state.get("scenario", ""),
            prev_level3_intent=state.get("level3_intent", ""),
            prev_pending_action=state.get("pending_action", ""),
            prev_current_step=state.get("current_step", "idle"),
            prev_business_reason=state.get("business_reason", ""),
            prev_problem_description=state.get("problem_description", ""),
            prev_pending_tasks_json=state.get("pending_tasks_json", "[]"),
        )

    tracking = route(
        {
            "scenario": "tracking",
            "confidence": 0.98,
            "waybill_no": "JT123456781",
            "missing_slots": [],
            "need_human": "false",
            "tool_name": "create_ticket",
        },
        "帮我查一下 JT123456781",
    )
    assert tracking["tool_name"] == "query_track", "模型不得越权选择写 Tool。"
    assert tracking["need_human"] == "false", "字符串 false 不得被当作 true。"

    missing = route(
        {"scenario": "tracking", "missing_slots": ["waybill_no"], "need_human": False},
        "帮我查快递",
    )
    assert missing["current_step"] == "waiting_waybill"
    continued = route(
        {"scenario": "fallback", "missing_slots": [], "need_human": False},
        "JT123456781",
        scenario=missing["scenario"],
        current_step=missing["current_step"],
    )
    assert continued["scenario"] == "tracking" and continued["tool_name"] == "query_track"

    phone_only = route(
        {"scenario": "tracking", "phone": "13800138000", "need_human": False},
        "手机号是13800138000，帮我查件",
    )
    assert phone_only["phone"] == "13800138000" and not phone_only["waybill_no"]

    expedite = route(
        {
            "scenario": "delivery_followup",
            "waybill_no": "JT123456786",
            "business_reason": "物流长时间未更新",
            "need_human": False,
        },
        "催件 JT123456786",
    )
    assert expedite["tool_name"] == "query_expedite_snapshot"
    snapshot = query_expedite(
        expedite["waybill_no"], expedite["level3_intent"], "催件 JT123456786",
        expedite["pending_task_hint"], expedite["pending_task_ready_hint"]
    )
    assert snapshot["normalized_status"] == "SELF_PICKUP"
    assert snapshot["next_step"] == "waiting_self_pickup_confirmation"

    pickup_no = route(
        {"scenario": "delivery_followup", "need_human": False},
        "否",
        waybill_no=expedite["waybill_no"],
        scenario="delivery_followup",
        current_step=snapshot["next_step"],
    )
    assert pickup_no["current_step"] == "waiting_expedite_phone"
    with_phone = route(
        {"scenario": "delivery_followup", "need_human": False},
        "13800138000",
        waybill_no=pickup_no["waybill_no"],
        scenario="delivery_followup",
        current_step=pickup_no["current_step"],
    )
    assert with_phone["current_step"] == "waiting_expedite_description"
    with_description = route(
        {"scenario": "delivery_followup", "need_human": False},
        "并非本人要求自取，希望继续派送",
        waybill_no=with_phone["waybill_no"],
        phone=with_phone["phone"],
        scenario="delivery_followup",
        current_step=with_phone["current_step"],
    )
    assert with_description["pending_action"] == "create_expedite_work_order"
    confirmed = route(
        {"scenario": "fallback", "need_human": False},
        "确认",
        waybill_no=with_description["waybill_no"],
        phone=with_description["phone"],
        scenario=with_description["scenario"],
        pending_action=with_description["pending_action"],
        current_step=with_description["current_step"],
        business_reason=with_description["business_reason"],
        problem_description=with_description["problem_description"],
    )
    assert confirmed["tool_name"] == "create_expedite_work_order"
    assert confirmed["problem_description"] == "并非本人要求自取，希望继续派送"
    not_created = create_expedite_work_order(
        confirmed["waybill_no"], confirmed["phone"], confirmed["problem_description"], "preview-conversation"
    )
    assert not_created["tool_status"] == "failed"
    assert "没有提交真实工单" in not_created["flow_reply"]

    signed_no = route(
        {"scenario": "delivery_followup", "need_human": False},
        "未收到",
        waybill_no="JT123456788",
        scenario="delivery_followup",
        current_step="waiting_signed_confirmation",
    )
    assert signed_no["scenario"] == "delivered_not_received"
    assert signed_no["current_step"] == "signed_not_received_fallback"

    status_cases = {
        "JT123456700": "INVALID_WAYBILL",
        "JT123456710": "TRACK_EMPTY",
        "JT123456711": "IN_TRANSIT",
        "JT123456714": "OUT_FOR_DELIVERY",
        "JT123456716": "SELF_PICKUP",
        "JT123456718": "SIGNED",
        "JT123456719": "UNKNOWN",
        "JT123456799": "SYSTEM_ERROR",
    }
    for waybill_no, status in status_cases.items():
        assert query_expedite(waybill_no, "urge_delivery", "催件", "", "")["normalized_status"] == status

    complaint_pending = route(
        {"scenario": "complaint", "waybill_no": "JT123456781", "need_human": False},
        "投诉运单 JT123456781",
    )
    assert complaint_pending["pending_action"] == "create_ticket"
    complaint_confirmed = route(
        {"scenario": "fallback", "need_human": False},
        "确认",
        waybill_no=complaint_pending["waybill_no"],
        scenario=complaint_pending["scenario"],
        pending_action=complaint_pending["pending_action"],
        current_step=complaint_pending["current_step"],
    )
    created = run_tool(
        complaint_confirmed["waybill_no"], complaint_confirmed["phone"],
        complaint_confirmed["scenario"], complaint_confirmed["tool_name"],
        "确认", complaint_confirmed["business_reason"],
    )
    assert created["tool_status"] == "success"

    human = route({"scenario": "complaint", "need_human": False}, "我要转人工客服")
    assert human["need_human"] == "true" and human["current_step"] == "human_handoff"

    assert fast_path(
        query="我不要查快递，想改地址",
        prev_scenario="tracking",
        prev_level3_intent="query_current_status",
        prev_current_step="waiting_waybill",
        prev_pending_action="",
        prev_pending_tasks_json="[]",
    )["use_llm"] == "true", "否定、纠正和切换不得被单句规则吞掉。"

    no_human = ConversationHarness()
    no_human_result = no_human.turn(
        "我不要转人工，帮我查 JT123456781",
        {
            "intents": [{
                "level2": "tracking", "level3": "query_current_status",
                "confidence": 0.98, "polarity": "affirmed",
            }],
            "intent_relation": "correction",
            "dialogue_act": "correct",
            "slots": {"waybill_no": "JT123456781"},
            "need_human": False,
        },
    )
    assert no_human_result["need_human"] == "false"
    assert no_human.called_tools == ["query_track"]

    malformed_model = ConversationHarness()
    malformed_result = malformed_model.turn("不要投诉，只查快递 JT123456781", {})
    assert malformed_result["scenario"] == "tracking"
    assert malformed_model.called_tools == ["query_track"]

    deterministic_waybill = ConversationHarness(
        scenario="tracking",
        level3_intent="query_current_status",
        current_step="waiting_waybill",
    )
    deterministic_waybill.turn("JT123456781")
    assert "llm_understand" not in deterministic_waybill.visited_nodes
    assert deterministic_waybill.called_tools == ["query_track"]
    assert deterministic_waybill.state["current_step"] == "completed"

    deterministic_phone = ConversationHarness(
        scenario="delivery_followup",
        level3_intent="self_pickup_not_requested",
        current_step="waiting_expedite_phone",
        waybill_no="JT123456786",
    )
    phone_result = deterministic_phone.turn("13800138000")
    assert "llm_understand" not in deterministic_phone.visited_nodes
    assert phone_result["current_step"] == "waiting_expedite_description"
    assert deterministic_phone.called_tools == []

    busy = ConversationHarness(
        scenario="delivery_followup",
        level3_intent="self_pickup_not_requested",
        current_step="waiting_expedite_phone",
        waybill_no="JT123456786",
    )
    busy_result = busy.turn(
        "先帮我查另一个单 JT123456781",
        {
            "intents": [{
                "level2": "tracking", "level3": "query_current_status",
                "confidence": 0.95, "polarity": "affirmed",
            }],
            "intent_relation": "parallel",
            "dialogue_act": "new_request",
            "slots": {"waybill_no": "JT123456781"},
            "need_human": False,
        },
    )
    assert busy_result["waybill_no"] == "JT123456786", "新待办的运单不得覆盖当前未完成 SOP 的运单。"
    busy_queue = json.loads(busy.state["pending_tasks_json"])
    assert busy_queue[0]["waybill_no"] == "JT123456781"
    assert busy.called_tools == []

    complaint_semantic = {
        "intents": [
            {
                "level2": "complaint",
                "level3": "general_complaint",
                "confidence": 0.96,
                "polarity": "affirmed",
            }
        ],
        "intent_relation": "single",
        "dialogue_act": "new_request",
        "slots": {"waybill_no": "JT123456781"},
        "missing_slots": [],
        "need_human": False,
        "business_reason": "用户要投诉该运单",
    }
    guarded = ConversationHarness()
    first_guarded = guarded.turn("我要投诉 JT123456781", complaint_semantic)
    assert first_guarded["pending_action"] == "create_ticket"
    assert guarded.called_tools == []
    guarded.turn("确认")
    assert "llm_understand" not in guarded.visited_nodes
    assert guarded.called_tools == ["create_ticket"]
    guarded.turn("确认")
    assert guarded.called_tools == [], "重复确认不得再次调用写 Tool。"

    multi_semantic = {
        "intents": [
            {
                "level2": "tracking",
                "level3": "query_current_status",
                "confidence": 0.97,
                "polarity": "affirmed",
            },
            {
                "level2": "change_address",
                "level3": "change_receiver_address",
                "confidence": 0.94,
                "polarity": "affirmed",
            },
        ],
        "intent_relation": "parallel",
        "dialogue_act": "new_request",
        "slots": {"waybill_no": "JT123456781"},
        "missing_slots": [],
        "need_human": False,
        "business_reason": "用户同时要查件和改址",
    }
    multi = ConversationHarness()
    first_task = multi.turn("我要查快递和改地址 JT123456781", multi_semantic)
    queued = json.loads(multi.state["pending_tasks_json"])
    assert first_task["level2_intent"] == "tracking"
    assert multi.called_tools == ["query_track"]
    assert len(queued) == 1 and queued[0]["level2"] == "change_address"
    assert queued[0]["waybill_no"] == "JT123456781", "同一句中明确运单号应安全共享给后续任务。"
    second_task = multi.turn("继续")
    assert "llm_understand" not in multi.visited_nodes
    assert second_task["level2_intent"] == "change_address"
    assert multi.called_tools == ["change_address_check"]
    assert json.loads(multi.state["pending_tasks_json"]) == []

    different_waybills = ConversationHarness()
    different_waybills.turn(
        "查 JT123456781，然后改 JT123456784 的地址",
        {
            "intents": [
                {
                    "level2": "tracking", "level3": "query_current_status",
                    "confidence": 0.98, "polarity": "affirmed",
                    "slots": {"waybill_no": "JT123456781"},
                },
                {
                    "level2": "change_address", "level3": "change_receiver_address",
                    "confidence": 0.96, "polarity": "affirmed",
                    "slots": {"waybill_no": "JT123456784"},
                },
            ],
            "intent_relation": "sequential",
            "dialogue_act": "new_request",
            "slots": {},
            "need_human": False,
        },
    )
    assert different_waybills.state["waybill_no"] == "JT123456781"
    different_queue = json.loads(different_waybills.state["pending_tasks_json"])
    assert different_queue[0]["waybill_no"] == "JT123456784"
    promoted_different = different_waybills.turn("继续")
    assert promoted_different["waybill_no"] == "JT123456784"
    assert different_waybills.called_tools == ["change_address_check"]

    alternative_semantic = dict(multi_semantic)
    alternative_semantic["intent_relation"] = "alternative"
    alternative = ConversationHarness()
    alternative_result = alternative.turn("查快递或者改地址", alternative_semantic)
    assert alternative_result["direct_reply"] == "true"
    assert alternative.called_tools == []
    assert json.loads(alternative.state["pending_tasks_json"]) == []
    assert "选择关系" in alternative_result["clarify_question"]

    invalid_pair = ConversationHarness()
    invalid_pair_result = invalid_pair.turn(
        "帮我查快递",
        {
            "intents": [
                {
                    "level2": "tracking",
                    "level3": "claim_loss",
                    "confidence": 0.99,
                    "polarity": "affirmed",
                }
            ],
            "intent_relation": "single",
            "dialogue_act": "new_request",
            "slots": {},
            "need_human": False,
        },
    )
    assert invalid_pair_result["tool_name"] == "none" and invalid_pair_result["direct_reply"] == "true"

    uncertain = ConversationHarness()
    uncertain_result = uncertain.turn(
        "我可能想投诉吧",
        {
            "intents": [{
                "level2": "complaint", "level3": "general_complaint",
                "confidence": 0.55, "polarity": "uncertain",
            }],
            "intent_relation": "single",
            "dialogue_act": "new_request",
            "slots": {},
            "need_human": False,
        },
    )
    assert uncertain_result["tool_name"] == "none"
    assert uncertain_result["pending_action"] == ""
    assert uncertain.called_tools == []

    invalid_waybill = ConversationHarness()
    invalid_result = invalid_waybill.turn(
        "帮我催一下 JT123456700",
        {
            "intents": [
                {
                    "level2": "delivery_followup",
                    "level3": "urge_delivery",
                    "confidence": 0.96,
                    "polarity": "affirmed",
                }
            ],
            "intent_relation": "single",
            "dialogue_act": "new_request",
            "slots": {"waybill_no": "JT123456700"},
            "need_human": False,
        },
    )
    assert invalid_result["tool_name"] == "query_expedite_snapshot"
    assert invalid_waybill.last_tool_result is not None
    assert invalid_waybill.last_tool_result["normalized_status"] == "INVALID_WAYBILL"
    assert invalid_waybill.last_tool_result["tool_status"] == "business_rejected"
    assert json.loads(invalid_waybill.last_tool_result["tool_result"])["success"] is False
    assert invalid_waybill.state["current_step"] == "waiting_waybill"
    assert invalid_waybill.state["waybill_no"] == ""
    recovered = invalid_waybill.turn("JT123456711")
    assert "llm_understand" not in invalid_waybill.visited_nodes
    assert recovered["level3_intent"] == "urge_delivery"
    assert invalid_waybill.called_tools == ["query_expedite_snapshot"]
    assert invalid_waybill.last_tool_result is not None
    assert invalid_waybill.last_tool_result["normalized_status"] == "IN_TRANSIT"

    eta = query_expedite("JT123456711", "ask_delivery_eta", "还要多久到", "", "")
    assert "无法给出准确送达时间" in eta["flow_reply"]
    repeated = query_expedite("JT123456711", "ask_delivery_eta", "还要多久到", "", "")
    assert eta["tool_result"] == repeated["tool_result"], "相同预览运单重复查询不得伪造新轨迹。"


def main() -> None:
    path, data = load_target()
    assert data.get("version") == "0.3.0", "DSL 版本发生变化，请重新核对 Π 平台兼容性。"
    assert data.get("app", {}).get("mode") == "advanced-chat"
    workflow = data["workflow"]
    nodes = validate_graph(workflow)
    validate_references(workflow, nodes)
    validate_state_and_routing_contract(workflow, nodes)
    smoke_test(nodes)
    print(f"PASS: {path.name}；{len(nodes)} 个节点，{len(workflow['graph']['edges'])} 条边。")


if __name__ == "__main__":
    main()
