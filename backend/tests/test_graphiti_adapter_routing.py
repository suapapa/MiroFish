from types import SimpleNamespace

from app.utils import graphiti_adapter as adapter


def test_fetch_group_items_uses_graph_database(monkeypatch):
    calls = []

    def fake_run_with_read_driver(coro_factory, database=None, timeout=None):
        calls.append(database)
        return []

    monkeypatch.setattr(adapter, "_run_with_read_driver", fake_run_with_read_driver)

    result = adapter._fetch_group_items_with_retry(
        lambda driver: [],
        resource="nodes",
        graph_id="graph-1",
        view_factory=lambda items: items,
    )

    assert result == []
    assert calls == ["graph-1"]


def test_create_initializes_graph_specific_graphiti(monkeypatch):
    calls = []

    def fake_get_graphiti(database=None):
        calls.append(database)
        return object()

    monkeypatch.setattr(adapter, "_get_graphiti", fake_get_graphiti)

    view = adapter._GraphNamespace().create(graph_id="graph-2")

    assert view.uuid == "graph-2"
    assert calls == ["graph-2"]


def test_add_episode_runs_against_graph_database(monkeypatch):
    calls = []

    def fake_run_with_graphiti(coro_factory, database=None, timeout=None):
        calls.append((database, timeout))
        return SimpleNamespace(episode=SimpleNamespace(uuid="episode-1"))

    monkeypatch.setattr(adapter, "_run_with_graphiti", fake_run_with_graphiti)
    monkeypatch.setattr(
        adapter._ontology_store,
        "types_for",
        lambda graph_id: (None, None, None),
    )

    view = adapter._GraphNamespace()._add_episode("graph-3", "payload")

    assert view.uuid == "episode-1"
    assert calls == [("graph-3", 300.0)]
