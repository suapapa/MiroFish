"""
Shared OpenAI-compatible client factory.

Applies consistent retry, timeout, and connection-pooling settings across
direct SDK usage and Graphiti-managed OpenAI-compatible calls.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse
from threading import BoundedSemaphore, Lock

import httpx
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI

from ..config import Config


_OPENAI_HOSTS = {
    "api.openai.com",
}

_TRUTHY = {"1", "true", "yes", "on", "always"}
_FALSY = {"0", "false", "no", "off", "never"}
_REQUEST_SEMAPHORES: dict[str, tuple[int, BoundedSemaphore]] = {}
_REQUEST_SEMAPHORES_LOCK = Lock()


class _LimitedHttpxClient(DefaultHttpxClient):
    def __init__(
        self,
        *args,
        request_semaphore: BoundedSemaphore | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._request_semaphore = request_semaphore

    def send(self, request, *args, **kwargs):
        if self._request_semaphore is None:
            return super().send(request, *args, **kwargs)
        self._request_semaphore.acquire()
        try:
            return super().send(request, *args, **kwargs)
        finally:
            self._request_semaphore.release()


class _LimitedAsyncHttpxClient(DefaultAsyncHttpxClient):
    def __init__(
        self,
        *args,
        request_semaphore: BoundedSemaphore | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._request_semaphore = request_semaphore

    async def send(self, request, *args, **kwargs):
        if self._request_semaphore is None:
            return await super().send(request, *args, **kwargs)
        await asyncio.to_thread(self._request_semaphore.acquire)
        try:
            return await super().send(request, *args, **kwargs)
        finally:
            self._request_semaphore.release()


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


def _get_max_concurrent_requests(client_kind: str) -> int | None:
    limit = {
        "llm": Config.LLM_MAX_CONCURRENT_REQUESTS,
        "graphiti_llm": Config.GRAPHITI_LLM_MAX_CONCURRENT_REQUESTS,
        "embedder": Config.EMBEDDER_MAX_CONCURRENT_REQUESTS,
    }.get(client_kind, Config.LLM_MAX_CONCURRENT_REQUESTS)
    return limit if limit > 0 else None


def _get_request_semaphore(client_kind: str) -> BoundedSemaphore | None:
    limit = _get_max_concurrent_requests(client_kind)
    if limit is None:
        return None

    with _REQUEST_SEMAPHORES_LOCK:
        cached = _REQUEST_SEMAPHORES.get(client_kind)
        if cached is None or cached[0] != limit:
            cached = (limit, BoundedSemaphore(limit))
            _REQUEST_SEMAPHORES[client_kind] = cached
        return cached[1]


def _build_timeout() -> httpx.Timeout:
    total = Config.LLM_REQUEST_TIMEOUT_SECONDS
    connect = min(Config.LLM_CONNECT_TIMEOUT_SECONDS, total)
    return httpx.Timeout(total, connect=connect)


def _build_limits(
    force_close: bool,
    max_concurrent_requests: int | None = None,
) -> httpx.Limits:
    max_connections = Config.LLM_MAX_CONNECTIONS
    keepalive_limit = 0 if force_close else Config.LLM_MAX_KEEPALIVE_CONNECTIONS
    if max_concurrent_requests is not None:
        max_connections = min(max_connections, max_concurrent_requests)
        keepalive_limit = min(keepalive_limit, max_connections)
    return httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=keepalive_limit,
    )


def _build_common_options(
    *,
    api_key: str | None,
    base_url: str | None,
    connection_close_mode: str | None = None,
    client_kind: str = "llm",
) -> tuple[dict, dict]:
    force_close = should_force_connection_close(
        base_url=base_url,
        mode=connection_close_mode,
    )
    max_concurrent_requests = _get_max_concurrent_requests(client_kind)

    http_client_kwargs = {
        "limits": _build_limits(
            force_close,
            max_concurrent_requests=max_concurrent_requests,
        ),
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
    client_kind: str = "llm",
) -> OpenAI:
    client_kwargs, http_client_kwargs = _build_common_options(
        api_key=api_key,
        base_url=base_url,
        connection_close_mode=connection_close_mode,
        client_kind=client_kind,
    )
    return OpenAI(
        **client_kwargs,
        http_client=_LimitedHttpxClient(
            **http_client_kwargs,
            request_semaphore=_get_request_semaphore(client_kind),
        ),
    )


def build_async_openai_client(
    *,
    api_key: str | None,
    base_url: str | None,
    connection_close_mode: str | None = None,
    client_kind: str = "llm",
) -> AsyncOpenAI:
    client_kwargs, http_client_kwargs = _build_common_options(
        api_key=api_key,
        base_url=base_url,
        connection_close_mode=connection_close_mode,
        client_kind=client_kind,
    )
    return AsyncOpenAI(
        **client_kwargs,
        http_client=_LimitedAsyncHttpxClient(
            **http_client_kwargs,
            request_semaphore=_get_request_semaphore(client_kind),
        ),
    )
