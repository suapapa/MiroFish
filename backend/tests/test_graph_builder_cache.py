from types import SimpleNamespace

from app.services import graph_builder as graph_builder_module
from app.services.graph_builder import GraphBuilderService
from app.utils import graph_cache as graph_cache_module


def _make_service():
    service = GraphBuilderService.__new__(GraphBuilderService)
    service.client = object()
    service.task_manager = None
    return service


def _node(uuid_, name, labels):
    return SimpleNamespace(
        uuid_=uuid_,
        name=name,
        labels=labels,
        summary="",
        attributes={},
        created_at=None,
    )


def test_get_graph_data_ignores_empty_cache_and_refreshes_live_data(monkeypatch):
    service = _make_service()
    saved = []

    monkeypatch.setattr(
        graph_cache_module,
        "load_graph_cache",
        lambda graph_id: {
            "graph_id": graph_id,
            "nodes": [],
            "edges": [],
            "node_count": 0,
            "edge_count": 0,
        },
    )
    monkeypatch.setattr(
        graph_cache_module,
        "save_graph_cache",
        lambda graph_id, data: saved.append((graph_id, data)),
    )
    monkeypatch.setattr(
        graph_builder_module,
        "fetch_all_nodes",
        lambda client, graph_id: [_node("node-1", "Server", ["Entity", "Asset"])],
    )
    monkeypatch.setattr(
        graph_builder_module,
        "fetch_all_edges",
        lambda client, graph_id: [],
    )

    result = service.get_graph_data("graph-1", use_cache=True)

    assert result["node_count"] == 1
    assert result["nodes"][0]["name"] == "Server"
    assert saved == [("graph-1", result)]


def test_get_graph_data_does_not_persist_empty_live_snapshot_without_existing_cache(monkeypatch):
    service = _make_service()
    saved = []

    monkeypatch.setattr(graph_cache_module, "load_graph_cache", lambda graph_id: None)
    monkeypatch.setattr(
        graph_cache_module,
        "save_graph_cache",
        lambda graph_id, data: saved.append((graph_id, data)),
    )
    monkeypatch.setattr(graph_builder_module, "fetch_all_nodes", lambda client, graph_id: [])
    monkeypatch.setattr(graph_builder_module, "fetch_all_edges", lambda client, graph_id: [])

    result = service.get_graph_data("graph-2", use_cache=False)

    assert result["node_count"] == 0
    assert result["edge_count"] == 0
    assert saved == []


def test_get_graph_data_keeps_existing_non_empty_cache_on_transient_empty_live_fetch(monkeypatch):
    service = _make_service()
    saved = []
    existing_cache = {
        "graph_id": "graph-3",
        "nodes": [
            {
                "uuid": "node-1",
                "name": "Cluster",
                "labels": ["Entity", "Infra"],
                "summary": "",
                "attributes": {},
                "created_at": None,
            }
        ],
        "edges": [
            {
                "uuid": "edge-1",
                "name": "CONNECTS",
                "fact": "",
                "fact_type": "CONNECTS",
                "source_node_uuid": "node-1",
                "target_node_uuid": "node-1",
                "source_node_name": "Cluster",
                "target_node_name": "Cluster",
                "attributes": {},
                "created_at": None,
                "valid_at": None,
                "invalid_at": None,
                "expired_at": None,
                "episodes": [],
            }
        ],
        "node_count": 1,
        "edge_count": 1,
    }

    monkeypatch.setattr(graph_cache_module, "load_graph_cache", lambda graph_id: existing_cache)
    monkeypatch.setattr(
        graph_cache_module,
        "save_graph_cache",
        lambda graph_id, data: saved.append((graph_id, data)),
    )
    monkeypatch.setattr(graph_builder_module, "fetch_all_nodes", lambda client, graph_id: [])
    monkeypatch.setattr(graph_builder_module, "fetch_all_edges", lambda client, graph_id: [])

    result = service.get_graph_data("graph-3", use_cache=False)

    assert result == existing_cache
    assert saved == []
