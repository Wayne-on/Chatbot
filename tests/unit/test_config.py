from customer_service_agent.config import Settings


def test_deepseek_specific_key_enables_model_without_generic_key() -> None:
    settings = Settings(
        _env_file=None,
        model_name="deepseek-v4-flash",
        model_base_url="https://api.deepseek.com",
        deepseek_api_key="sk-test-not-used",
    )
    assert settings.model_enabled is True
    assert settings.model_api_key is None
    assert settings.effective_model_api_key is not None
    assert "sk-test-not-used" not in repr(settings)
