"""
Ontology normalization and validation.

When the LLM returns malformed structure in json_object mode (nested entity/edge,
schema violations), recover and validate structure before graph build.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from ..utils.logger import get_logger

logger = get_logger("mirofish.ontology_utils")

_RESERVED_ENTITY_KEYS = frozenset({
    "name", "description", "attributes", "examples",
    "entity_types", "edge_types", "analysis_summary", "type",
})


def to_pascal_case(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    words: List[str] = []
    for part in parts:
        words.extend(re.sub(r"([a-z])([A-Z])", r"\1_\2", part).split("_"))
    result = "".join(word.capitalize() for word in words if word)
    return result if result else "Unknown"


def _normalize_attributes(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []

    normalized: List[Dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            continue
        if not isinstance(item, dict):
            continue

        if isinstance(item.get("name"), str) and item["name"].strip():
            normalized.append({
                "name": item["name"].strip(),
                "type": str(item.get("type") or "text"),
                "description": str(item.get("description") or item["name"]),
            })
            continue

        for key, val in item.items():
            if key in _RESERVED_ENTITY_KEYS or not isinstance(key, str) or not key.strip():
                continue
            if not isinstance(val, dict):
                continue
            normalized.append({
                "name": key.strip(),
                "type": str(val.get("type") or "text"),
                "description": str(val.get("description") or key),
            })

    return normalized


def _normalize_examples(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if isinstance(x, str) and x.strip()]


def _normalize_source_targets(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []

    normalized: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        target = item.get("target")
        if isinstance(source, str) and source.strip() and isinstance(target, str) and target.strip():
            normalized.append({"source": source.strip(), "target": target.strip()})
    return normalized


def _entity_from_dict(data: dict, fallback_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if "source_targets" in data:
        return None

    name = data.get("name") or fallback_name
    if not isinstance(name, str) or not name.strip():
        return None

    if not any(key in data for key in ("description", "attributes", "examples")):
        return None

    description = data.get("description", f"A {name} entity.")
    if not isinstance(description, str):
        description = str(description) if description else f"A {name} entity."
    if len(description) > 100:
        description = description[:97] + "..."

    return {
        "name": name.strip(),
        "description": description,
        "attributes": _normalize_attributes(data.get("attributes")),
        "examples": _normalize_examples(data.get("examples")),
    }


def _edge_from_dict(data: dict) -> Optional[Dict[str, Any]]:
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if "source_targets" not in data:
        return None

    source_targets = _normalize_source_targets(data.get("source_targets"))
    if not source_targets:
        return None

    description = data.get("description", f"A {name} relationship.")
    if not isinstance(description, str):
        description = str(description) if description else f"A {name} relationship."
    if len(description) > 100:
        description = description[:97] + "..."

    return {
        "name": name.strip().upper(),
        "description": description,
        "source_targets": source_targets,
        "attributes": _normalize_attributes(data.get("attributes")),
    }


def _find_analysis_summary(node: Any) -> Optional[str]:
    if isinstance(node, dict):
        summary = node.get("analysis_summary")
        if isinstance(summary, str) and summary.strip():
            return summary
        for value in node.values():
            found = _find_analysis_summary(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_analysis_summary(item)
            if found:
                return found
    return None


def _walk_and_collect(
    node: Any,
    entities: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    seen_entity_names: Set[str],
    seen_edge_names: Set[str],
    parent_key: Optional[str] = None,
) -> None:
    if isinstance(node, dict):
        edge = _edge_from_dict(node)
        if edge:
            if edge["name"] not in seen_edge_names:
                seen_edge_names.add(edge["name"])
                edges.append(edge)
        else:
            entity = _entity_from_dict(node, fallback_name=parent_key)
            if entity:
                pascal_name = to_pascal_case(entity["name"])
                if pascal_name not in seen_entity_names:
                    seen_entity_names.add(pascal_name)
                    entity["name"] = pascal_name
                    entities.append(entity)

        for key, value in node.items():
            if key == "analysis_summary":
                continue
            next_parent = parent_key
            if (
                isinstance(key, str)
                and key not in _RESERVED_ENTITY_KEYS
                and isinstance(value, dict)
                and "source_targets" not in value
            ):
                next_parent = key
            _walk_and_collect(
                value, entities, edges, seen_entity_names, seen_edge_names, next_parent
            )
    elif isinstance(node, list):
        for item in node:
            _walk_and_collect(
                item, entities, edges, seen_entity_names, seen_edge_names, parent_key=None
            )


def _looks_well_formed(ontology: Dict[str, Any]) -> bool:
    entities = ontology.get("entity_types") or []
    edges = ontology.get("edge_types") or []
    if not isinstance(entities, list) or not isinstance(edges, list):
        return False
    if not entities:
        return False

    for entity in entities:
        if not isinstance(entity, dict):
            return False
        if not isinstance(entity.get("name"), str) or not entity["name"].strip():
            return False
        for attr in entity.get("attributes") or []:
            if not isinstance(attr, dict) or not isinstance(attr.get("name"), str):
                return False

    for edge in edges:
        if not isinstance(edge, dict):
            return False
        if not isinstance(edge.get("name"), str) or not edge["name"].strip():
            return False
        if not _normalize_source_targets(edge.get("source_targets")):
            return False

    return True


def normalize_ontology(ontology: Any) -> Dict[str, Any]:
    """Normalize LLM-returned ontology into standard structure."""
    if not isinstance(ontology, dict):
        return {"entity_types": [], "edge_types": [], "analysis_summary": ""}

    if _looks_well_formed(ontology):
        summary = ontology.get("analysis_summary", "")
        if not isinstance(summary, str):
            summary = _find_analysis_summary(ontology) or ""
        return {
            "entity_types": ontology.get("entity_types") or [],
            "edge_types": ontology.get("edge_types") or [],
            "analysis_summary": summary,
        }

    logger.warning("Malformed ontology detected; attempting structural recovery")

    entities: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    seen_entity_names: Set[str] = set()
    seen_edge_names: Set[str] = set()

    for entity in ontology.get("entity_types") or []:
        if isinstance(entity, dict):
            parsed = _entity_from_dict(entity)
            if parsed:
                pascal_name = to_pascal_case(parsed["name"])
                if pascal_name not in seen_entity_names:
                    seen_entity_names.add(pascal_name)
                    parsed["name"] = pascal_name
                    entities.append(parsed)

    for edge in ontology.get("edge_types") or []:
        if isinstance(edge, dict):
            parsed = _edge_from_dict(edge)
            if parsed and parsed["name"] not in seen_edge_names:
                seen_edge_names.add(parsed["name"])
                edges.append(parsed)

    if len(entities) < 2 or not edges:
        _walk_and_collect(
            ontology, entities, edges, seen_entity_names, seen_edge_names
        )

    summary = ontology.get("analysis_summary", "")
    if not isinstance(summary, str) or not summary.strip():
        summary = _find_analysis_summary(ontology) or ""

    return {
        "entity_types": entities,
        "edge_types": edges,
        "analysis_summary": summary,
    }


def validate_ontology(ontology: Dict[str, Any]) -> Optional[str]:
    """Return an error code string if invalid."""
    entities = ontology.get("entity_types") or []
    edges = ontology.get("edge_types") or []

    if not entities:
        return "ontologyInvalidNoEntities"

    for entity in entities:
        if not isinstance(entity, dict):
            return "ontologyInvalidEntity"
        if not isinstance(entity.get("name"), str) or not entity["name"].strip():
            return "ontologyInvalidEntityName"
        for attr in entity.get("attributes") or []:
            if not isinstance(attr, dict) or not isinstance(attr.get("name"), str):
                return "ontologyInvalidEntityAttributes"

    for edge in edges:
        if not isinstance(edge, dict):
            return "ontologyInvalidEdge"
        if not isinstance(edge.get("name"), str) or not edge["name"].strip():
            return "ontologyInvalidEdgeName"
        if not _normalize_source_targets(edge.get("source_targets")):
            return "ontologyInvalidEdgeTargets"

    return None
