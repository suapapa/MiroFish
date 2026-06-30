from app.config import Config
from app.utils.openai_client_factory import (
    _build_common_options,
    _get_max_concurrent_requests,
    should_force_connection_close,
)


def test_should_force_connection_close_auto_for_third_party_provider():
    assert should_force_connection_close(
        base_url="https://llm.homin.dev/v1",
        mode="auto",
    )


def test_should_force_connection_close_auto_for_openai_official_host():
    assert not should_force_connection_close(
        base_url="https://api.openai.com/v1",
        mode="auto",
    )


def test_should_force_connection_close_respects_explicit_override():
    assert should_force_connection_close(
        base_url="https://api.openai.com/v1",
        mode="always",
    )
    assert not should_force_connection_close(
        base_url="https://llm.homin.dev/v1",
        mode="never",
    )


def test_build_common_options_disables_keepalive_for_third_party_provider():
    client_kwargs, http_client_kwargs = _build_common_options(
        api_key="test-key",
        base_url="https://llm.homin.dev/v1",
        connection_close_mode="auto",
    )

    assert client_kwargs["max_retries"] >= 1
    assert http_client_kwargs["headers"] == {"Connection": "close"}
    assert http_client_kwargs["limits"].max_keepalive_connections == 0


def test_build_common_options_keeps_pooling_for_openai_official_host():
    _, http_client_kwargs = _build_common_options(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        connection_close_mode="auto",
    )

    assert "headers" not in http_client_kwargs
    assert http_client_kwargs["limits"].max_keepalive_connections > 0


def test_get_max_concurrent_requests_uses_service_specific_limits(monkeypatch):
    monkeypatch.setattr(Config, "LLM_MAX_CONCURRENT_REQUESTS", 7)
    monkeypatch.setattr(Config, "GRAPHITI_LLM_MAX_CONCURRENT_REQUESTS", 3)
    monkeypatch.setattr(Config, "EMBEDDER_MAX_CONCURRENT_REQUESTS", 11)

    assert _get_max_concurrent_requests("llm") == 7
    assert _get_max_concurrent_requests("graphiti_llm") == 3
    assert _get_max_concurrent_requests("embedder") == 11


def test_build_common_options_caps_pool_size_to_concurrency_limit(monkeypatch):
    monkeypatch.setattr(Config, "LLM_MAX_CONNECTIONS", 20)
    monkeypatch.setattr(Config, "LLM_MAX_KEEPALIVE_CONNECTIONS", 20)
    monkeypatch.setattr(Config, "GRAPHITI_LLM_MAX_CONCURRENT_REQUESTS", 2)

    _, http_client_kwargs = _build_common_options(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        connection_close_mode="never",
        client_kind="graphiti_llm",
    )

    assert http_client_kwargs["limits"].max_connections == 2
    assert http_client_kwargs["limits"].max_keepalive_connections == 2
