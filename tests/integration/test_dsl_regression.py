import json
from pathlib import Path

from customer_service_agent.schemas import ChatRequest


async def test_dsl_regression_cases(container) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "dsl_regression_cases.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    for index, case in enumerate(cases):
        response = None
        for message in case["messages"]:
            response = await container.agent.ainvoke(
                ChatRequest(
                    session_id=f"regression-{index}",
                    user_id="fixture-user",
                    message=message,
                )
            )
        assert response is not None
        if "expected_intent" in case:
            assert response.current_intent == case["expected_intent"], case["name"]
        if "expected_status" in case:
            assert response.status == case["expected_status"], case["name"]
        if "expected_action" in case:
            assert response.action_required == case["expected_action"], case["name"]
        for expected_text in case.get("expected_reply_contains", []):
            assert expected_text in response.reply, case["name"]
