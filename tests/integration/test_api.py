import httpx

from customer_service_agent.main import create_app


async def test_health_ready_and_chat(container) -> None:
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        interface = await client.get("/")
        assert interface.status_code == 200
        assert "Logistics Copilot" in interface.text
        assert "/static/app.js" in interface.text
        stylesheet = await client.get("/static/styles.css")
        assert stylesheet.status_code == 200
        assert "--accent" in stylesheet.text
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/ready")).json() == {"status": "ready"}
        response = await client.post(
            "/v1/chat",
            json={
                "session_id": "api-1",
                "user_id": "user-1",
                "message": "Where is my package? JT123456781",
                "language": "en",
                "user_credential": "short-lived-secret",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["current_intent"] == "tracking"
    assert response.headers["X-Trace-ID"] == body["trace_id"]
    assert "short-lived-secret" not in response.text
