"""
Graph build service
API 2: Build knowledge graph with Graphiti (FalkorDB), self-hosted (replaces Zep Cloud)
"""

import os
import uuid
import time
import threading
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..utils.graphiti_adapter import GraphitiClient as Zep, EpisodeData

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges
from .text_processor import TextProcessor
from ..utils.locale import t, get_locale, set_locale
from ..utils.logger import get_logger

logger = get_logger('mirofish.graph_builder')


@dataclass
class GraphInfo:
    """Graph metadata"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


class GraphBuilderService:
    """
    Graph build service
    Calls Zep-compatible API to build the knowledge graph
    """
    
    def __init__(self, api_key: Optional[str] = None):
        # api_key kept for backward-compatible signature; self-hosted Graphiti(FalkorDB) does not need it
        self.api_key = api_key
        self.client = Zep(api_key=self.api_key)
        self.task_manager = TaskManager()
    
    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = Config.DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = Config.DEFAULT_CHUNK_OVERLAP,
        batch_size: int = Config.DEFAULT_GRAPH_BUILD_BATCH_SIZE
    ) -> str:
        """
        Build graph asynchronously

        Args:
            text: Input text
            ontology: Ontology definition (from API 1 output)
            graph_name: Graph name
            chunk_size: Text chunk size
            chunk_overlap: Chunk overlap size
            batch_size: Chunks per batch

        Returns:
            Task ID
        """
        # Create task
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )
        
        # Capture locale before spawning background thread
        current_locale = get_locale()

        # Run build in background thread
        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size, current_locale)
        )
        thread.daemon = True
        thread.start()
        
        return task_id
    
    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int,
        locale: str = 'zh'
    ):
        """Graph build worker thread"""
        set_locale(locale)
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message=t('progress.startBuildingGraph')
            )
            
            # 1. Create graph
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=t('progress.graphCreated', graphId=graph_id)
            )
            
            # 2. Set ontology
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message=t('progress.ontologySet')
            )
            
            # 3. Split text into chunks
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=t('progress.textSplit', count=total_chunks)
            )
            
            # 4. Send data in batches
            episode_uuids = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.4),  # 20-60%
                    message=msg
                )
            )
            
            # 5. Wait for Zep/Graphiti processing
            self.task_manager.update_task(
                task_id,
                progress=60,
                message=t('progress.waitingZepProcess')
            )
            
            self._wait_for_episodes(
                episode_uuids,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=60 + int(prog * 0.3),  # 60-90%
                    message=msg
                )
            )
            
            # 6. Fetch graph info
            self.task_manager.update_task(
                task_id,
                progress=90,
                message=t('progress.fetchingGraphInfo')
            )
            
            graph_info = self._get_graph_info(graph_id)
            
            # Complete
            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })
            
        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)
    
    def create_graph(self, name: str) -> str:
        """Create Zep/Graphiti graph (public API)"""
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"
        
        self.client.graph.create(
            graph_id=graph_id,
            name=name,
            description="MiroFish Social Simulation Graph"
        )
        
        return graph_id
    
    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """Set graph ontology (public API)

        Persists entity/edge type definitions per graph_id; Graphiti builds Pydantic
        models and edge_type_map from them on each episode write.
        """
        self.client.graph.set_ontology(graph_ids=[graph_id], ontology=ontology)
    
    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = Config.DEFAULT_GRAPH_BUILD_BATCH_SIZE,
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """Add text to graph in batches; return all episode UUIDs"""
        episode_uuids = []
        total_chunks = len(chunks)
        
        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size
            
            if progress_callback:
                progress = (i + len(batch_chunks)) / total_chunks
                progress_callback(
                    t('progress.sendingBatch', current=batch_num, total=total_batches, chunks=len(batch_chunks)),
                    progress
                )
            
            # Build episode payloads
            episodes = [
                EpisodeData(data=chunk, type="text")
                for chunk in batch_chunks
            ]
            
            # Send to Zep (with retry)
            max_batch_retries = 3
            batch_result = None
            last_batch_error = None

            for attempt in range(max_batch_retries + 1):
                try:
                    batch_result = self.client.graph.add_batch(
                        graph_id=graph_id,
                        episodes=episodes
                    )
                    last_batch_error = None
                    break
                except Exception as e:
                    last_batch_error = e
                    if attempt >= max_batch_retries:
                        if progress_callback:
                            progress_callback(
                                t('progress.batchFailed', batch=batch_num, error=str(e)),
                                (i + len(batch_chunks)) / total_chunks
                            )
                        raise

                    retry_delay = 2 ** attempt
                    logger.warning(
                        f"Batch {batch_num} attempt {attempt + 1} failed: {str(e)}, "
                        f"retrying in {retry_delay}s..."
                    )
                    if progress_callback:
                        progress_callback(
                            t('progress.batchRetry', batch=batch_num, attempt=attempt + 1, delay=retry_delay),
                            (i + len(batch_chunks)) / total_chunks
                        )
                    time.sleep(retry_delay)

            if last_batch_error:
                raise last_batch_error

            # Collect returned episode UUIDs
            if batch_result and isinstance(batch_result, list):
                for ep in batch_result:
                    ep_uuid = getattr(ep, 'uuid_', None) or getattr(ep, 'uuid', None)
                    if ep_uuid:
                        episode_uuids.append(ep_uuid)

            # Avoid sending requests too fast
            time.sleep(1)
        
        return episode_uuids
    
    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600
    ):
        """Wait until all episodes are processed (poll each episode's processed flag)"""
        if not episode_uuids:
            if progress_callback:
                progress_callback(t('progress.noEpisodesWait'), 1.0)
            return
        
        start_time = time.time()
        pending_episodes = set(episode_uuids)
        completed_count = 0
        total_episodes = len(episode_uuids)
        
        if progress_callback:
            progress_callback(t('progress.waitingEpisodes', count=total_episodes), 0)
        
        while pending_episodes:
            if time.time() - start_time > timeout:
                if progress_callback:
                    progress_callback(
                        t('progress.episodesTimeout', completed=completed_count, total=total_episodes),
                        completed_count / total_episodes
                    )
                break
            
            # Check processing status for each episode
            for ep_uuid in list(pending_episodes):
                try:
                    episode = self.client.graph.episode.get(uuid_=ep_uuid)
                    is_processed = getattr(episode, 'processed', False)
                    
                    if is_processed:
                        pending_episodes.remove(ep_uuid)
                        completed_count += 1
                        
                except Exception as e:
                    # Ignore single-query errors and continue
                    pass
            
            elapsed = int(time.time() - start_time)
            if progress_callback:
                progress_callback(
                    t('progress.zepProcessing', completed=completed_count, total=total_episodes, pending=len(pending_episodes), elapsed=elapsed),
                    completed_count / total_episodes if total_episodes > 0 else 0
                )
            
            if pending_episodes:
                time.sleep(3)  # Poll every 3 seconds
        
        if progress_callback:
            progress_callback(t('progress.processingComplete', completed=completed_count, total=total_episodes), 1.0)
    
    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """Fetch graph summary info"""
        # Fetch nodes (paginated)
        nodes = fetch_all_nodes(self.client, graph_id)

        # Fetch edges (paginated)
        edges = fetch_all_edges(self.client, graph_id)

        # Collect entity type labels
        entity_types = set()
        for node in nodes:
            if node.labels:
                for label in node.labels:
                    if label not in ["Entity", "Node"]:
                        entity_types.add(label)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types)
        )
    
    def get_graph_data(self, graph_id: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        Fetch full graph data (detailed)

        Args:
            graph_id: Graph ID
            use_cache: Prefer cache persisted at build time

        Returns:
            Dict with nodes and edges including timestamps, attributes, etc.
        """
        from ..utils.graph_cache import load_graph_cache, save_graph_cache

        if use_cache:
            cached = load_graph_cache(graph_id)
            if cached and cached.get('nodes') is not None:
                logger.info(f"Loaded graph data from cache: graph={graph_id}")
                return cached

        nodes = fetch_all_nodes(self.client, graph_id)
        edges = fetch_all_edges(self.client, graph_id)

        # Node map for resolving names on edges
        node_map = {}
        for node in nodes:
            node_map[node.uuid_] = node.name or ""
        
        nodes_data = []
        for node in nodes:
            # Created timestamp
            created_at = getattr(node, 'created_at', None)
            if created_at:
                created_at = str(created_at)
            
            nodes_data.append({
                "uuid": node.uuid_,
                "name": node.name,
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
                "created_at": created_at,
            })
        
        edges_data = []
        for edge in edges:
            # Temporal fields
            created_at = getattr(edge, 'created_at', None)
            valid_at = getattr(edge, 'valid_at', None)
            invalid_at = getattr(edge, 'invalid_at', None)
            expired_at = getattr(edge, 'expired_at', None)
            
            # Episodes
            episodes = getattr(edge, 'episodes', None) or getattr(edge, 'episode_ids', None)
            if episodes and not isinstance(episodes, list):
                episodes = [str(episodes)]
            elif episodes:
                episodes = [str(e) for e in episodes]
            
            # fact_type
            fact_type = getattr(edge, 'fact_type', None) or edge.name or ""
            
            edges_data.append({
                "uuid": edge.uuid_,
                "name": edge.name or "",
                "fact": edge.fact or "",
                "fact_type": fact_type,
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "source_node_name": node_map.get(edge.source_node_uuid, ""),
                "target_node_name": node_map.get(edge.target_node_uuid, ""),
                "attributes": edge.attributes or {},
                "created_at": str(created_at) if created_at else None,
                "valid_at": str(valid_at) if valid_at else None,
                "invalid_at": str(invalid_at) if invalid_at else None,
                "expired_at": str(expired_at) if expired_at else None,
                "episodes": episodes or [],
            })
        
        result = {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }

        # During build FalkorDB may briefly return empty — do not overwrite existing cache
        if len(nodes_data) == 0:
            existing = load_graph_cache(graph_id)
            existing_nodes = existing.get("node_count", len(existing.get("nodes", []))) if existing else 0
            if existing_nodes > 0:
                logger.warning(
                    f"Live fetch returned empty graph but cache has {existing_nodes} nodes "
                    f"(graph={graph_id}); keeping cached snapshot"
                )
                return existing

        save_graph_cache(graph_id, result)
        return result
    
    def delete_graph(self, graph_id: str):
        """Delete graph"""
        self.client.graph.delete(graph_id=graph_id)
