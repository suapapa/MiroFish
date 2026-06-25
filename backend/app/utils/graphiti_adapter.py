"""
Graphiti + FalkorDB 适配层（替代 Zep Cloud）

本模块用一个本地自托管的 Graphiti（时序知识图谱引擎，后端为 FalkorDB）
来替换原先依赖的 Zep Cloud 服务，并对外暴露一套与 Zep SDK 兼容的同步接口
（`client.graph.*`），从而让其余业务代码（graph_builder / zep_tools /
zep_entity_reader / zep_graph_memory_updater / oasis_profile_generator）
几乎无需改动即可切换。

设计要点：
1. Graphiti 是全异步 API，这里用一个常驻后台事件循环线程把协程包装成同步调用，
   方便在 Flask/gunicorn 的同步 worker 中使用。
2. Zep 的 `graph_id` 映射为 Graphiti 的 `group_id`（多租户隔离）。
3. Ontology（实体/边类型）按 graph_id 持久化到磁盘，并在每次写入 episode 时
   重新构建 Graphiti 所需的 Pydantic 模型传入抽取流程。
4. 返回对象用轻量包装类，提供与 Zep 一致的字段（uuid_/name/labels/summary/
   fact/source_node_uuid/valid_at/invalid_at/expired_at 等）。
"""

from __future__ import annotations

import os
import json
import asyncio
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, create_model

from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.graphiti_adapter')


# ── 与 zep_cloud 兼容的占位异常（zep_paging 等模块按此重试网络/IO 错误）──
class InternalServerError(Exception):
    """图存储端瞬态错误（兼容原 zep_cloud.InternalServerError 的重试语义）。"""


# ── 与 zep_cloud 兼容的数据载体 ──
class EpisodeData:
    """对应 zep_cloud.EpisodeData：一段待写入图谱的文本。"""

    def __init__(self, data: str, type: str = "text"):
        self.data = data
        self.type = type


class EntityEdgeSourceTarget:
    """对应 zep_cloud.EntityEdgeSourceTarget：边的源/目标实体类型约束。"""

    def __init__(self, source: str = "Entity", target: str = "Entity"):
        self.source = source
        self.target = target


# ════════════════════════════════════════════════════════════════
# 异步事件循环桥接：在后台线程跑一个常驻 loop，把协程变成同步调用
# ════════════════════════════════════════════════════════════════

class _AsyncRunner:
    """常驻后台事件循环，提供 run(coro) 同步执行协程。"""

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

    def run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    @classmethod
    def instance(cls) -> "_AsyncRunner":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


def _run(coro):
    return _AsyncRunner.instance().run(coro)


# ════════════════════════════════════════════════════════════════
# 轻量返回对象（兼容 Zep 字段访问）
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
    """add_batch 返回项 / episode.get 返回项。Graphiti 写入即处理完成。"""

    def __init__(self, uuid: str = ''):
        self.uuid = uuid
        self.uuid_ = uuid
        self.processed = True


class _SearchView:
    def __init__(self, edges: List[_EdgeView], nodes: List[_NodeView]):
        self.edges = edges
        self.nodes = nodes


# ════════════════════════════════════════════════════════════════
# Ontology 管理：按 graph_id 持久化 + 动态构建 Graphiti Pydantic 模型
# ════════════════════════════════════════════════════════════════

# Graphiti/图存储保留字段，不能作为实体/边的属性名
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
    """从本体定义构建 Graphiti 实体类型（Pydantic BaseModel 子类）。"""
    entity_types: Dict[str, type] = {}
    for entity_def in ontology.get("entity_types", []) or []:
        name = (entity_def.get("name") or "").strip()
        if not name:
            logger.warning("Skipping entity type definition missing name")
            continue
        description = entity_def.get("description", f"A {name} entity.")
        fields: Dict[str, Any] = {}
        for attr_def in entity_def.get("attributes", []) or []:
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
    """从本体定义构建 Graphiti 边类型与 edge_type_map。"""
    edge_types: Dict[str, type] = {}
    edge_type_map: Dict[tuple, List[str]] = {}
    for edge_def in ontology.get("edge_types", []) or []:
        name = (edge_def.get("name") or "").strip()
        if not name:
            logger.warning("Skipping edge type definition missing name")
            continue
        description = edge_def.get("description", f"A {name} relationship.")
        fields: Dict[str, Any] = {}
        for attr_def in edge_def.get("attributes", []) or []:
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
            key = (st.get("source", "Entity"), st.get("target", "Entity"))
            edge_type_map.setdefault(key, []).append(name)

    return edge_types, edge_type_map


class _OntologyStore:
    """进程内缓存 + 磁盘持久化的本体仓库。"""

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
# Graphiti 单例
# ════════════════════════════════════════════════════════════════

_graphiti = None
_graphiti_lock = threading.Lock()


def _create_graphiti():
    """惰性创建 Graphiti 实例（FalkorDB 驱动 + OpenAI 兼容 LLM/Embedder）。"""
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

    llm_config = LLMConfig(
        api_key=Config.LLM_API_KEY,
        base_url=Config.LLM_BASE_URL,
        model=Config.LLM_MODEL_NAME,
        small_model=Config.LLM_SMALL_MODEL_NAME,
    )

    # 选择 LLM 客户端：
    # OpenAIClient 依赖 OpenAI 专有的 Responses API（responses.parse），第三方兼容端点
    # （如阿里云 qwen/dashscope）无法支持，会返回截断/非法 JSON 导致实体/边抽取失败：
    #   "Invalid JSON: EOF while parsing a string" / "Source entity not found in nodes"。
    # 默认改用 OpenAIGenericClient（标准 /chat/completions + json_object），兼容性更好。
    if Config.GRAPHITI_LLM_CLIENT == 'openai':
        from graphiti_core.llm_client.openai_client import OpenAIClient
        llm_client = OpenAIClient(config=llm_config)
    else:
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
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

    # cross-encoder 复用 LLM 做重排；搜索默认走 RRF，不强依赖它
    cross_encoder = OpenAIRerankerClient(config=llm_config)

    graphiti = Graphiti(
        graph_driver=driver,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=cross_encoder,
    )

    # 构建索引/约束（幂等）
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
# 与 Zep SDK 兼容的命名空间
# ════════════════════════════════════════════════════════════════

class _EpisodeNamespace:
    """对应 zep_cloud client.graph.episode。"""

    def get(self, uuid_: str = '', **kwargs) -> _EpisodeView:
        # Graphiti 的 add_episode 是同步完成的，episode 写入即已处理
        return _EpisodeView(uuid=uuid_)


class _NodeNamespace:
    """对应 zep_cloud client.graph.node。"""

    def get_by_graph_id(
        self,
        graph_id: str,
        limit: int = 100,
        uuid_cursor: Optional[str] = None,
        **kwargs,
    ) -> List[_NodeView]:
        from graphiti_core.nodes import EntityNode

        async def _do():
            g = _get_graphiti()
            return await EntityNode.get_by_group_ids(
                g.driver, [graph_id], limit=limit, uuid_cursor=uuid_cursor
            )

        try:
            nodes = _run(_do())
        except Exception as e:
            raise InternalServerError(str(e)) from e
        return [_NodeView(n) for n in (nodes or [])]

    def get(self, uuid_: str = '', **kwargs) -> Optional[_NodeView]:
        from graphiti_core.nodes import EntityNode

        async def _do():
            g = _get_graphiti()
            return await EntityNode.get_by_uuid(g.driver, uuid_)

        node = _run(_do())
        return _NodeView(node) if node else None

    def get_entity_edges(self, node_uuid: str = '', **kwargs) -> List[_EdgeView]:
        from graphiti_core.edges import EntityEdge

        async def _do():
            g = _get_graphiti()
            return await EntityEdge.get_by_node_uuid(g.driver, node_uuid)

        edges = _run(_do())
        return [_EdgeView(e) for e in (edges or [])]


class _EdgeNamespace:
    """对应 zep_cloud client.graph.edge。"""

    def get_by_graph_id(
        self,
        graph_id: str,
        limit: int = 100,
        uuid_cursor: Optional[str] = None,
        **kwargs,
    ) -> List[_EdgeView]:
        from graphiti_core.edges import EntityEdge

        async def _do():
            g = _get_graphiti()
            return await EntityEdge.get_by_group_ids(
                g.driver, [graph_id], limit=limit, uuid_cursor=uuid_cursor
            )

        try:
            edges = _run(_do())
        except Exception as e:
            raise InternalServerError(str(e)) from e
        return [_EdgeView(e) for e in (edges or [])]


class _GraphNamespace:
    """对应 zep_cloud client.graph。"""

    def __init__(self):
        self.episode = _EpisodeNamespace()
        self.node = _NodeNamespace()
        self.edge = _EdgeNamespace()

    # ── 图谱生命周期 ──
    def create(self, graph_id: str = '', name: str = '', description: str = '', **kwargs):
        """Graphiti 中 group 是惰性的，这里仅确保实例就绪并返回标识。"""
        _get_graphiti()
        return _EpisodeView(uuid=graph_id)

    def delete(self, graph_id: str = '', **kwargs):
        """删除该 group_id 下的全部节点/边（及其本体定义）。"""
        async def _do():
            g = _get_graphiti()
            await g.driver.execute_query(
                "MATCH (n {group_id: $gid}) DETACH DELETE n",
                gid=graph_id,
            )

        try:
            _run(_do())
        finally:
            _ontology_store.delete(graph_id)

    def set_ontology(self, graph_ids: Optional[List[str]] = None, ontology: Optional[Dict[str, Any]] = None, **kwargs):
        """保存本体定义（按 graph_id 持久化），写入 episode 时自动应用。"""
        ontology = ontology or {}
        for gid in (graph_ids or []):
            _ontology_store.save(gid, ontology)

    # ── 写入 ──
    def _add_episode(self, graph_id: str, text: str, name: str = "episode"):
        from graphiti_core.nodes import EpisodeType

        entity_types, edge_types, edge_type_map = _ontology_store.types_for(graph_id)

        async def _do():
            g = _get_graphiti()
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

        result = _run(_do())
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

    # ── 检索 ──
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
        async def _do():
            g = _get_graphiti()
            return await g.search(query, group_ids=[graph_id], num_results=limit)

        edges = _run(_do())
        return _SearchView(edges=[_EdgeView(e) for e in (edges or [])], nodes=[])

    def _search_nodes(self, graph_id: str, query: str, limit: int) -> _SearchView:
        from graphiti_core.search.search_config_recipes import NODE_HYBRID_SEARCH_RRF

        async def _do():
            g = _get_graphiti()
            cfg = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
            cfg.limit = limit
            return await g._search(query, cfg, group_ids=[graph_id])

        results = _run(_do())
        nodes = getattr(results, 'nodes', []) or []
        return _SearchView(edges=[], nodes=[_NodeView(n) for n in nodes])


class GraphitiClient:
    """对应 zep_cloud.client.Zep 的入口对象。

    用法保持一致：client = GraphitiClient(); client.graph.search(...)。
    `api_key` 参数仅为兼容旧签名，自托管模式下忽略。
    """

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        self.graph = _GraphNamespace()


# 兼容别名：旧代码以 `from zep_cloud.client import Zep` 导入
Zep = GraphitiClient
