"""
Shared OpenAI-compatible client factory.

Applies consistent retry, timeout, and connection-pooling settings across
direct SDK usage and Graphiti-managed OpenAI-compatible calls.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI

from ..config import Config


_OPENAI_HOSTS = {
    "api.openai.com",
}

_TRUTHY = {"1", "true", "yes", "on", "always"}
_FALSY = {"0", "false", "no", "off", "never"}


def should_force_connection_close(
    *,
    base_url: str | None,
    mode: str | None = None,
) -> bool:
    """
    Decide whether to disable keep-alive for this provider.

    `auto` mode is conservative: keep the default behavior for the official
    OpenAI host and force connection close for third-party OpenAI-compatible
    endpoints, which are more likely to mishandle stale pooled connections.
    """
    normalized_mode = (mode or Config.LLM_CONNECTION_CLOSE).strip().lower()

    if normalized_mode in _TRUTHY:
        return True
    if normalized_mode in _FALSY:
        return False

    hostname = (urlparse(base_url or "").hostname or "").lower()
    return hostname not in _OPENAI_HOSTS


def _build_timeout() -> httpx.Timeout:
    total = Config.LLM_REQUEST_TIMEOUT_SECONDS
    connect = min(Config.LLM_CONNECT_TIMEOUT_SECONDS, total)
    return httpx.Timeout(total, connect=connect)


def _build_limits(force_close: bool) -> httpx.Limits:
    keepalive_limit = 0 if force_close else Config.LLM_MAX_KEEPALIVE_CONNECTIONS
    return httpx.Limits(
        max_connections=Config.LLM_MAX_CONNECTIONS,
        max_keepalive_connections=keepalive_limit,
    )


def _build_common_options(
    *,
    api_key: str | None,
    base_url: str | None,
    connection_close_mode: str | None = None,
) -> tuple[dict, dict]:
    force_close = should_force_connection_close(
        base_url=base_url,
        mode=connection_close_mode,
    )

    http_client_kwargs = {
        "limits": _build_limits(force_close),
        "timeout": _build_timeout(),
    }
    if force_close:
        http_client_kwargs["headers"] = {"Connection": "close"}

    client_kwargs = {
        "api_key": api_key,
        "base_url": base_url,
        "max_retries": Config.LLM_MAX_RETRIES,
        "timeout": _build_timeout(),
    }

    return client_kwargs, http_client_kwargs


def build_openai_client(
    *,
    api_key: str | None,
    base_url: str | None,
    connection_close_mode: str | None = None,
) -> OpenAI:
    client_kwargs, http_client_kwargs = _build_common_options(
        api_key=api_key,
        base_url=base_url,
        connection_close_mode=connection_close_mode,
    )
    return OpenAI(
        **client_kwargs,
        http_client=DefaultHttpxClient(**http_client_kwargs),
    )


def build_async_openai_client(
    *,
    api_key: str | None,
    base_url: str | None,
    connection_close_mode: str | None = None,
) -> AsyncOpenAI:
    client_kwargs, http_client_kwargs = _build_common_options(
        api_key=api_key,
        base_url=base_url,
        connection_close_mode=connection_close_mode,
    )
    return AsyncOpenAI(
        **client_kwargs,
        http_client=DefaultAsyncHttpxClient(**http_client_kwargs),
    )
