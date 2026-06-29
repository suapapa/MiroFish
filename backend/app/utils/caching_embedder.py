"""
In-process LRU cache for Graphiti embedder calls.

Graphiti's add_episode pipeline may embed the same node name or edge fact multiple
times (dedup search, hybrid search query, persistence). This wrapper deduplicates
those calls within a process.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from threading import Lock
from typing import Any

from graphiti_core.embedder import EmbedderClient

from .logger import get_logger

logger = get_logger('mirofish.caching_embedder')


def _normalize_cache_key(text: str) -> str:
    """Match graphiti's single-text embed normalization."""
    return text.replace('\n', ' ')


class CachingEmbedder(EmbedderClient):
    """EmbedderClient wrapper with LRU caching keyed by normalized text."""

    def __init__(self, inner: EmbedderClient, *, max_size: int = 10_000):
        self._inner = inner
        self._max_size = max(1, max_size)
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _cache_get(self, key: str) -> list[float] | None:
        with self._lock:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            return value

    def _cache_set(self, key: str, embedding: list[float]) -> None:
        with self._lock:
            self._cache[key] = embedding
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        if isinstance(input_data, str):
            key = _normalize_cache_key(input_data)
            cached = self._cache_get(key)
            if cached is not None:
                logger.debug('embedder cache hit (create): %d chars', len(key))
                return cached
            embedding = await self._inner.create(input_data)
            self._cache_set(key, embedding)
            return embedding

        if isinstance(input_data, list) and len(input_data) == 1 and isinstance(input_data[0], str):
            key = _normalize_cache_key(input_data[0])
            cached = self._cache_get(key)
            if cached is not None:
                logger.debug('embedder cache hit (create): %d chars', len(key))
                return cached
            embedding = await self._inner.create(input_data)
            self._cache_set(key, embedding)
            return embedding

        return await self._inner.create(input_data)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []

        keys = [_normalize_cache_key(text) for text in input_data_list]
        results: list[list[float] | None] = [self._cache_get(key) for key in keys]

        miss_indices: list[int] = []
        miss_texts: list[str] = []
        for index, (result, text) in enumerate(zip(results, input_data_list, strict=True)):
            if result is None:
                miss_indices.append(index)
                miss_texts.append(text)

        if not miss_texts:
            logger.debug('embedder cache hit (create_batch): %d/%d', len(input_data_list), len(input_data_list))
            return results  # type: ignore[return-value]

        fetched = await self._inner.create_batch(miss_texts)
        hits = len(input_data_list) - len(miss_texts)
        if hits:
            logger.debug(
                'embedder cache partial hit (create_batch): %d/%d',
                hits,
                len(input_data_list),
            )

        for index, text, embedding in zip(miss_indices, miss_texts, fetched, strict=True):
            self._cache_set(_normalize_cache_key(text), embedding)
            results[index] = embedding

        return results  # type: ignore[return-value]
