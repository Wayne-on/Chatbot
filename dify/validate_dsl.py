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
    for edge in graph["edges"]:
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


def smoke_test(nodes: dict[str, dict[str, Any]]) -> None:
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

    def route(llm: dict[str, Any], query: str, **state: str) -> dict[str, Any]:
        return parse(
            llm_text=json.dumps(llm, ensure_ascii=False),
            query=query,
            prev_waybill=state.get("waybill_no", ""),
            prev_phone=state.get("phone", ""),
            prev_scenario=state.get("scenario", ""),
            prev_pending_action=state.get("pending_action", ""),
            prev_current_step=state.get("current_step", "idle"),
            prev_business_reason=state.get("business_reason", ""),
            prev_problem_description=state.get("problem_description", ""),
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
    snapshot = query_expedite(expedite["waybill_no"])
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
        assert query_expedite(waybill_no)["normalized_status"] == status

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


def main() -> None:
    path, data = load_target()
    assert data.get("version") == "0.3.0", "DSL 版本发生变化，请重新核对 Π 平台兼容性。"
    assert data.get("app", {}).get("mode") == "advanced-chat"
    workflow = data["workflow"]
    nodes = validate_graph(workflow)
    validate_references(workflow, nodes)
    smoke_test(nodes)
    print(f"PASS: {path.name}；{len(nodes)} 个节点，{len(workflow['graph']['edges'])} 条边。")


if __name__ == "__main__":
    main()
