"""
Graphiti + FalkorDB adapter layer (replaces Zep Cloud)

This module uses a locally self-hosted Graphiti (temporal knowledge graph engine,
FalkorDB backend) instead of the former Zep Cloud service, and exposes a Zep SDK-
compatible synchronous interface (`client.graph.*`) so the rest of the codebase
(graph_builder / zep_tools / zep_entity_reader / zep_graph_memory_updater /
oasis_profile_generator) can switch with minimal changes.

Design notes:
1. Graphiti is fully async; a dedicated background event-loop thread wraps coroutines
   into sync calls for use in Flask/gunicorn sync workers.
2. Zep `graph_id` maps to Graphiti `group_id` (multi-tenant isolation).
3. Ontology (entity/edge types) is persisted per graph_id on disk and rebuilt into
   Graphiti Pydantic models on each episode write for extraction.
4. Return objects are lightweight wrappers with Zep-compatible fields (uuid_/name/
   labels/summary/fact/source_node_uuid/valid_at/invalid_at/expired_at, etc.).
"""

from __future__ import annotations

import os
import json
import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, create_model

from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.graphiti_adapter')


# ── zep_cloud-compatible placeholder exception (zep_paging retries network/IO errors) ──
class InternalServerError(Exception):
    """Transient graph-store error (compatible with zep_cloud.InternalServerError retry semantics)."""


def _empty_list_if_not_found(exc: Exception, resource: str) -> Optional[List[Any]]:
    """Graphiti(FalkorDB) errors when no nodes/edges exist; treat as empty during early build."""
    message = str(exc).lower()
    if f"no {resource} found" in message:
        return []
    return None


def _fetch_group_items_with_retry(
    fetch_coro_factory,
    resource: str,
    graph_id: str,
    view_factory,
    max_attempts: int = 3,
    retry_delay: float = 1.0,
) -> List[Any]:
    """Group query; 'not found' may be transient empty during writes — retry briefly then treat as empty."""
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        try:
            items = _run_with_graphiti(fetch_coro_factory)
            return view_factory(items or [])
        except Exception as e:
            if _empty_list_if_not_found(e, resource) is None:
                raise InternalServerError(str(e)) from e
            last_exc = e
            if attempt < max_attempts - 1:
                logger.debug(
                    f"Transient empty {resource} for graph={graph_id}, "
                    f"retry {attempt + 1}/{max_attempts - 1} in {retry_delay:.1f}s"
                )
                time.sleep(retry_delay)

    logger.debug(
        f"No {resource} found for graph={graph_id} after {max_attempts} attempts: {last_exc}"
    )
    return []


# ── zep_cloud-compatible data carriers ──
class EpisodeData:
    """Corresponds to zep_cloud.EpisodeData: text segment to write into the graph."""

    def __init__(self, data: str, type: str = "text"):
        self.data = data
        self.type = type


class EntityEdgeSourceTarget:
    """Corresponds to zep_cloud.EntityEdgeSourceTarget: source/target entity type constraints for edges."""

    def __init__(self, source: str = "Entity", target: str = "Entity"):
        self.source = source
        self.target = target


# ════════════════════════════════════════════════════════════════
# Async event-loop bridge: run a persistent loop in a background thread
# ════════════════════════════════════════════════════════════════

class _AsyncRunner:
    """Persistent background event loop; provides run(coro) for synchronous coroutine execution."""

    _instance: Optional["_AsyncRunner"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="graphiti-loop", daemon=True
        )
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro, timeout: float = 120.0):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError as e:
            future.cancel()
            raise TimeoutError(
                f"Graphiti async operation timed out after {timeout}s"
            ) from e

    @classmethod
    def instance(cls) -> "_AsyncRunner":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


def _run(coro, timeout: float = 120.0):
    return _AsyncRunner.instance().run(coro, timeout=timeout)


def _run_with_graphiti(coro_factory, timeout: float = 120.0):
    """Initialize Graphiti on the worker thread, then submit the coroutine — avoids nested _run deadlock on the loop thread."""
    graphiti = _get_graphiti()

    async def _do():
        return await coro_factory(graphiti)

    return _run(_do(), timeout=timeout)


# ════════════════════════════════════════════════════════════════
# Lightweight return objects (Zep-compatible field access)
# ════════════════════════════════════════════════════════════════

class _NodeView:
    def __init__(self, node: Any):
        self.uuid = getattr(node, 'uuid', '') or ''
        self.uuid_ = self.uuid
        self.name = getattr(node, 'name', '') or ''
        self.labels = list(getattr(node, 'labels', []) or [])
        self.summary = getattr(node, 'summary', '') or ''
        self.attributes = getattr(node, 'attributes', {}) or {}
        self.created_at = getattr(node, 'created_at', None)


class _EdgeView:
    def __init__(self, edge: Any):
        self.uuid = getattr(edge, 'uuid', '') or ''
        self.uuid_ = self.uuid
        self.name = getattr(edge, 'name', '') or ''
        self.fact = getattr(edge, 'fact', '') or ''
        self.source_node_uuid = getattr(edge, 'source_node_uuid', '') or ''
        self.target_node_uuid = getattr(edge, 'target_node_uuid', '') or ''
        self.attributes = getattr(edge, 'attributes', {}) or {}
        self.created_at = getattr(edge, 'created_at', None)
        self.valid_at = getattr(edge, 'valid_at', None)
        self.invalid_at = getattr(edge, 'invalid_at', None)
        self.expired_at = getattr(edge, 'expired_at', None)
        self.fact_type = getattr(edge, 'name', '') or ''
        self.episodes = list(getattr(edge, 'episodes', []) or [])


class _EpisodeView:
    """add_batch return item / episode.get return item. Graphiti completes processing on write."""

    def __init__(self, uuid: str = ''):
        self.uuid = uuid
        self.uuid_ = uuid
        self.processed = True


class _SearchView:
    def __init__(self, edges: List[_EdgeView], nodes: List[_NodeView]):
        self.edges = edges
        self.nodes = nodes


# ════════════════════════════════════════════════════════════════
# Ontology management: persist per graph_id + build Graphiti Pydantic models dynamically
# ════════════════════════════════════════════════════════════════

# Graphiti/graph-store reserved fields — cannot be used as entity/edge attribute names
_RESERVED_NAMES = {
    'uuid', 'name', 'group_id', 'name_embedding', 'summary',
    'created_at', 'labels', 'attributes', 'fact', 'episodes',
}


def _safe_attr_name(attr_name: str) -> str:
    if attr_name.lower() in _RESERVED_NAMES:
        return f"entity_{attr_name}"
    return attr_name


def _ontology_dir(graph_id: str) -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'graphs', graph_id)


def _ontology_path(graph_id: str) -> str:
    return os.path.join(_ontology_dir(graph_id), 'ontology.json')


def _edge_class_name(name: str) -> str:
    return ''.join(word.capitalize() for word in name.split('_')) or name


def _build_entity_types(ontology: Dict[str, Any]) -> Dict[str, type]:
    """Build Graphiti entity types (Pydantic BaseModel subclasses) from ontology definitions."""
    entity_types: Dict[str, type] = {}
    for entity_def in ontology.get("entity_types", []) or []:
        if not isinstance(entity_def, dict):
            logger.warning("Skipping non-dict entity type definition")
            continue
        name = (entity_def.get("name") or "").strip()
        if not name:
            logger.warning("Skipping entity type definition missing name")
            continue
        description = entity_def.get("description", f"A {name} entity.")
        fields: Dict[str, Any] = {}
        for attr_def in entity_def.get("attributes", []) or []:
            if not isinstance(attr_def, dict):
                continue
            raw_name = (attr_def.get("name") or "").strip()
            if not raw_name:
                continue
            attr_name = _safe_attr_name(raw_name)
            attr_desc = attr_def.get("description", attr_name)
            fields[attr_name] = (Optional[str], Field(default=None, description=attr_desc))
        model = create_model(name, __base__=BaseModel, **fields)
        model.__doc__ = description
        entity_types[name] = model
    return entity_types


def _build_edge_types(ontology: Dict[str, Any]) -> tuple[Dict[str, type], Dict[tuple, List[str]]]:
    """Build Graphiti edge types and edge_type_map from ontology definitions."""
    edge_types: Dict[str, type] = {}
    edge_type_map: Dict[tuple, List[str]] = {}
    for edge_def in ontology.get("edge_types", []) or []:
        if not isinstance(edge_def, dict):
            logger.warning("Skipping non-dict edge type definition")
            continue
        name = (edge_def.get("name") or "").strip()
        if not name:
            logger.warning("Skipping edge type definition missing name")
            continue
        description = edge_def.get("description", f"A {name} relationship.")
        fields: Dict[str, Any] = {}
        for attr_def in edge_def.get("attributes", []) or []:
            if not isinstance(attr_def, dict):
                continue
            raw_name = (attr_def.get("name") or "").strip()
            if not raw_name:
                continue
            attr_name = _safe_attr_name(raw_name)
            attr_desc = attr_def.get("description", attr_name)
            fields[attr_name] = (Optional[str], Field(default=None, description=attr_desc))
        model = create_model(_edge_class_name(name), __base__=BaseModel, **fields)
        model.__doc__ = description
        edge_types[name] = model

        for st in edge_def.get("source_targets", []) or []:
            if not isinstance(st, dict):
                continue
            key = (st.get("source", "Entity"), st.get("target", "Entity"))
            edge_type_map.setdefault(key, []).append(name)

    return edge_types, edge_type_map


class _OntologyStore:
    """In-process cache + disk-persisted ontology store."""

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def save(self, graph_id: str, ontology: Dict[str, Any]):
        with self._lock:
            self._cache[graph_id] = ontology
        try:
            os.makedirs(_ontology_dir(graph_id), exist_ok=True)
            with open(_ontology_path(graph_id), 'w', encoding='utf-8') as f:
                json.dump(ontology, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist ontology (graph={graph_id}): {e}")

    def load(self, graph_id: str) -> Dict[str, Any]:
        with self._lock:
            if graph_id in self._cache:
                return self._cache[graph_id]
        path = _ontology_path(graph_id)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    ontology = json.load(f)
                with self._lock:
                    self._cache[graph_id] = ontology
                return ontology
            except Exception as e:
                logger.warning(f"Failed to load ontology (graph={graph_id}): {e}")
        return {}

    def types_for(self, graph_id: str):
        ontology = self.load(graph_id)
        if not ontology:
            return None, None, None
        entity_types = _build_entity_types(ontology)
        edge_types, edge_type_map = _build_edge_types(ontology)
        return (
            entity_types or None,
            edge_types or None,
            edge_type_map or None,
        )

    def delete(self, graph_id: str):
        with self._lock:
            self._cache.pop(graph_id, None)
        import shutil
        d = _ontology_dir(graph_id)
        if os.path.isdir(d):
            try:
                shutil.rmtree(d)
            except Exception as e:
                logger.warning(f"Failed to delete ontology directory (graph={graph_id}): {e}")


_ontology_store = _OntologyStore()


# ════════════════════════════════════════════════════════════════
# Graphiti singleton
# ════════════════════════════════════════════════════════════════

_graphiti = None
_graphiti_lock = threading.Lock()


def _create_graphiti():
    """Lazily create Graphiti instance (FalkorDB driver + OpenAI-compatible LLM/Embedder)."""
    from graphiti_core import Graphiti
    from graphiti_core.driver.falkordb_driver import FalkorDriver
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

    driver = FalkorDriver(
        host=Config.GRAPH_DB_HOST,
        port=Config.GRAPH_DB_PORT,
        username=Config.GRAPH_DB_USERNAME,
        password=Config.GRAPH_DB_PASSWORD,
        database=Config.GRAPH_DB_NAME,
    )

    graphiti_model = Config.GRAPHITI_LLM_MODEL_NAME
    llm_config = LLMConfig(
        api_key=Config.LLM_API_KEY,
        base_url=Config.LLM_BASE_URL,
        model=graphiti_model,
        small_model=graphiti_model,
    )

    # Choose LLM client:
    # OpenAIClient relies on OpenAI's proprietary Responses API (responses.parse); third-party
    # compatible endpoints (e.g. Alibaba qwen/dashscope) cannot support it and return truncated/
    # invalid JSON causing entity/edge extraction to fail:
    #   "Invalid JSON: EOF while parsing a string" / "Source entity not found in nodes".
    # Therefore default to OpenAIGenericClient (standard /chat/completions). Its structured
    # output prefers json_schema by default; fall back to json_object only when the provider
    # explicitly does not support json_schema (via env var).
    if Config.GRAPHITI_LLM_CLIENT == 'openai':
        from graphiti_core.llm_client.openai_client import OpenAIClient
        llm_client = OpenAIClient(config=llm_config)
    else:
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
        if Config.LLM_STRUCTURED_OUTPUT_MODE == 'json_object':
            logger.warning(
                "Graphiti structured output mode is 'json_object'. Some OpenAI-compatible "
                "models may echo the JSON schema instead of returning extracted data. "
                "Prefer 'json_schema' unless your provider rejects it."
            )
        llm_client = OpenAIGenericClient(
            config=llm_config,
            structured_output_mode=Config.LLM_STRUCTURED_OUTPUT_MODE,
        )

    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key=Config.EMBEDDER_API_KEY,
            base_url=Config.EMBEDDER_BASE_URL,
            embedding_model=Config.EMBEDDER_MODEL_NAME,
            embedding_dim=Config.EMBEDDER_DIM,
        )
    )

    # cross-encoder reuses LLM for reranking; search defaults to RRF and does not strictly depend on it
    cross_encoder = OpenAIRerankerClient(config=llm_config)

    graphiti = Graphiti(
        graph_driver=driver,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=cross_encoder,
    )

    # Build indices/constraints (idempotent)
    _run(graphiti.build_indices_and_constraints())
    logger.info("Graphiti(FalkorDB) initialized")
    return graphiti


def _get_graphiti():
    global _graphiti
    if _graphiti is None:
        with _graphiti_lock:
            if _graphiti is None:
                _graphiti = _create_graphiti()
    return _graphiti


# ════════════════════════════════════════════════════════════════
# Zep SDK-compatible namespaces
# ════════════════════════════════════════════════════════════════

class _EpisodeNamespace:
    """Corresponds to zep_cloud client.graph.episode."""

    def get(self, uuid_: str = '', **kwargs) -> _EpisodeView:
        # Graphiti add_episode completes synchronously — episode is processed on write
        return _EpisodeView(uuid=uuid_)


class _NodeNamespace:
    """Corresponds to zep_cloud client.graph.node."""

    def get_by_graph_id(
        self,
        graph_id: str,
        limit: int = 100,
        uuid_cursor: Optional[str] = None,
        **kwargs,
    ) -> List[_NodeView]:
        from graphiti_core.nodes import EntityNode

        async def _do(g):
            return await EntityNode.get_by_group_ids(
                g.driver, [graph_id], limit=limit, uuid_cursor=uuid_cursor
            )

        return _fetch_group_items_with_retry(
            _do,
            resource="nodes",
            graph_id=graph_id,
            view_factory=lambda nodes: [_NodeView(n) for n in nodes],
        )

    def get(self, uuid_: str = '', **kwargs) -> Optional[_NodeView]:
        from graphiti_core.nodes import EntityNode

        async def _do(g):
            return await EntityNode.get_by_uuid(g.driver, uuid_)

        node = _run_with_graphiti(_do)
        return _NodeView(node) if node else None

    def get_entity_edges(self, node_uuid: str = '', **kwargs) -> List[_EdgeView]:
        from graphiti_core.edges import EntityEdge

        async def _do(g):
            return await EntityEdge.get_by_node_uuid(g.driver, node_uuid)

        edges = _run_with_graphiti(_do)
        return [_EdgeView(e) for e in (edges or [])]


class _EdgeNamespace:
    """Corresponds to zep_cloud client.graph.edge."""

    def get_by_graph_id(
        self,
        graph_id: str,
        limit: int = 100,
        uuid_cursor: Optional[str] = None,
        **kwargs,
    ) -> List[_EdgeView]:
        from graphiti_core.edges import EntityEdge

        async def _do(g):
            return await EntityEdge.get_by_group_ids(
                g.driver, [graph_id], limit=limit, uuid_cursor=uuid_cursor
            )

        return _fetch_group_items_with_retry(
            _do,
            resource="edges",
            graph_id=graph_id,
            view_factory=lambda edges: [_EdgeView(e) for e in edges],
        )


class _GraphNamespace:
    """Corresponds to zep_cloud client.graph."""

    def __init__(self):
        self.episode = _EpisodeNamespace()
        self.node = _NodeNamespace()
        self.edge = _EdgeNamespace()

    # ── Graph lifecycle ──
    def create(self, graph_id: str = '', name: str = '', description: str = '', **kwargs):
        """Groups are lazy in Graphiti; ensure instance is ready and return identifier."""
        _get_graphiti()
        return _EpisodeView(uuid=graph_id)

    def delete(self, graph_id: str = '', **kwargs):
        """Delete all nodes/edges (and ontology) under this group_id."""
        async def _do(g):
            await g.driver.execute_query(
                "MATCH (n {group_id: $gid}) DETACH DELETE n",
                gid=graph_id,
            )

        try:
            _run_with_graphiti(_do)
        finally:
            _ontology_store.delete(graph_id)
            from ..utils.graph_cache import delete_graph_cache
            delete_graph_cache(graph_id)

    def set_ontology(self, graph_ids: Optional[List[str]] = None, ontology: Optional[Dict[str, Any]] = None, **kwargs):
        """Save ontology definitions (persisted per graph_id); applied automatically on episode write."""
        ontology = ontology or {}
        for gid in (graph_ids or []):
            _ontology_store.save(gid, ontology)

    # ── Writes ──
    def _add_episode(self, graph_id: str, text: str, name: str = "episode"):
        from graphiti_core.nodes import EpisodeType

        entity_types, edge_types, edge_type_map = _ontology_store.types_for(graph_id)

        async def _do(g):
            return await g.add_episode(
                name=name,
                episode_body=text,
                source=EpisodeType.text,
                source_description="MiroFish",
                reference_time=datetime.now(timezone.utc),
                group_id=graph_id,
                entity_types=entity_types,
                edge_types=edge_types,
                edge_type_map=edge_type_map,
            )

        result = _run_with_graphiti(_do, timeout=300.0)
        ep = getattr(result, 'episode', None)
        ep_uuid = getattr(ep, 'uuid', '') if ep else ''
        return _EpisodeView(uuid=ep_uuid)

    def add(self, graph_id: str = '', type: str = "text", data: str = '', **kwargs) -> _EpisodeView:
        return self._add_episode(graph_id, data)

    def add_batch(self, graph_id: str = '', episodes: Optional[List[EpisodeData]] = None, **kwargs) -> List[_EpisodeView]:
        results: List[_EpisodeView] = []
        for i, ep in enumerate(episodes or []):
            data = getattr(ep, 'data', '') if not isinstance(ep, dict) else ep.get('data', '')
            results.append(self._add_episode(graph_id, data, name=f"episode_{i}"))
        return results

    # ── Retrieval ──
    def search(
        self,
        graph_id: str = '',
        query: str = '',
        limit: int = 10,
        scope: str = "edges",
        reranker: Optional[str] = None,
        **kwargs,
    ) -> _SearchView:
        if scope == "nodes":
            return self._search_nodes(graph_id, query, limit)
        return self._search_edges(graph_id, query, limit)

    def _search_edges(self, graph_id: str, query: str, limit: int) -> _SearchView:
        async def _do(g):
            return await g.search(query, group_ids=[graph_id], num_results=limit)

        edges = _run_with_graphiti(_do)
        return _SearchView(edges=[_EdgeView(e) for e in (edges or [])], nodes=[])

    def _search_nodes(self, graph_id: str, query: str, limit: int) -> _SearchView:
        from graphiti_core.search.search_config_recipes import NODE_HYBRID_SEARCH_RRF

        async def _do(g):
            cfg = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
            cfg.limit = limit
            return await g._search(query, cfg, group_ids=[graph_id])

        results = _run_with_graphiti(_do)
        nodes = getattr(results, 'nodes', []) or []
        return _SearchView(edges=[], nodes=[_NodeView(n) for n in nodes])


class GraphitiClient:
    """Entry object corresponding to zep_cloud.client.Zep.

    Usage unchanged: client = GraphitiClient(); client.graph.search(...).
    `api_key` is kept for backward-compatible signature only; ignored in self-hosted mode.
    """

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        self.graph = _GraphNamespace()


# Compatibility alias: legacy code imports via `from zep_cloud.client import Zep`
Zep = GraphitiClient
