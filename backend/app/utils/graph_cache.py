"""On-disk graph data cache: persist after build to avoid repeated full FalkorDB scans."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.graph_cache')


def graph_cache_path(graph_id: str) -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'graphs', graph_id, 'graph_data.json')


def load_graph_cache(graph_id: str) -> Optional[Dict[str, Any]]:
    path = graph_cache_path(graph_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        logger.warning(f"Failed to load graph cache (graph={graph_id}): {e}")
        return None


def save_graph_cache(graph_id: str, data: Dict[str, Any]) -> None:
    path = graph_cache_path(graph_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(
            f"Saved graph cache: graph={graph_id}, "
            f"nodes={data.get('node_count', len(data.get('nodes', [])))}, "
            f"edges={data.get('edge_count', len(data.get('edges', [])))}"
        )
    except Exception as e:
        logger.warning(f"Failed to save graph cache (graph={graph_id}): {e}")


def delete_graph_cache(graph_id: str) -> None:
    path = graph_cache_path(graph_id)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logger.warning(f"Failed to delete graph cache (graph={graph_id}): {e}")
