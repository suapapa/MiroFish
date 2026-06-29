import pytest

from app.utils.caching_embedder import CachingEmbedder, _normalize_cache_key


class _FakeEmbedder:
    def __init__(self):
        self.create_calls = 0
        self.create_batch_calls = 0
        self.config = object()

    async def create(self, input_data):
        self.create_calls += 1
        if isinstance(input_data, str):
            text = input_data
        elif isinstance(input_data, list) and input_data:
            text = input_data[0]
        else:
            raise AssertionError(f'unexpected create input: {input_data!r}')
        return [float(len(text))]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        self.create_batch_calls += 1
        return [[float(len(text))] for text in input_data_list]


@pytest.mark.asyncio
async def test_create_batch_reuses_cached_text():
    inner = _FakeEmbedder()
    embedder = CachingEmbedder(inner, max_size=10)

    first = await embedder.create_batch(['alpha', 'beta'])
    second = await embedder.create_batch(['alpha', 'gamma'])

    assert first == [[5.0], [4.0]]
    assert second == [[5.0], [5.0]]
    assert inner.create_batch_calls == 2
    assert inner.create_calls == 0


@pytest.mark.asyncio
async def test_create_and_create_batch_share_cache():
    inner = _FakeEmbedder()
    embedder = CachingEmbedder(inner, max_size=10)

    single = await embedder.create('hello')
    batch = await embedder.create_batch(['hello'])

    assert single == [5.0]
    assert batch == [[5.0]]
    assert inner.create_calls == 1
    assert inner.create_batch_calls == 0


@pytest.mark.asyncio
async def test_normalize_newlines_for_cache_key():
    inner = _FakeEmbedder()
    embedder = CachingEmbedder(inner, max_size=10)

    await embedder.create('line one\nline two')
    result = await embedder.create(['line one line two'])

    assert result == [float(len(_normalize_cache_key('line one\nline two')))]
    assert inner.create_calls == 1


@pytest.mark.asyncio
async def test_lru_eviction():
    inner = _FakeEmbedder()
    embedder = CachingEmbedder(inner, max_size=2)

    await embedder.create('a')
    await embedder.create('b')
    await embedder.create('c')  # evicts 'a'
    await embedder.create('b')  # cache hit
    await embedder.create('a')  # cache miss after eviction

    assert inner.create_calls == 4
