from customer_service_agent.middleware.security import (
    hash_subject,
    mask_user_id,
    redact_sensitive_text,
)
from customer_service_agent.schemas import ChatRequest


def test_sensitive_values_are_redacted() -> None:
    text = redact_sensitive_text("phone +84 912 345 678 bearer abc.def.ghi")
    assert "912" not in text
    assert "abc.def.ghi" not in text
    assert mask_user_id("user-001") == "us***01"
    assert hash_subject("user-001") != "user-001"


def test_credential_is_not_serialized_or_repr_exposed() -> None:
    request = ChatRequest(
        session_id="s1",
        user_id="u1",
        message="hello",
        user_credential="super-secret-token",
    )
    assert "super-secret-token" not in repr(request)
    assert "user_credential" not in request.model_dump()
