"""
OpenAPI annotations and Swagger UI routes for the MiroFish backend.

The route metadata in this module is intentionally written in English because
it is rendered directly in the generated API documentation.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from flask import Response, jsonify, request, url_for


OPENAPI_VERSION = "3.0.3"
DOCS_ROUTE = "/docs"
OPENAPI_JSON_ROUTE = f"{DOCS_ROUTE}/openapi.json"

_FLASK_PARAM_RE = re.compile(r"<(?:(?P<converter>[^:<>]+):)?(?P<name>[^<>]+)>")


def register_openapi_docs(app) -> None:
    """Register the OpenAPI JSON and Swagger UI endpoints."""

    @app.get(DOCS_ROUTE, strict_slashes=False)
    def openapi_docs_ui():
        spec_url = url_for("openapi_json")
        return Response(_swagger_ui_html(spec_url), mimetype="text/html")

    @app.get(OPENAPI_JSON_ROUTE)
    def openapi_json():
        return jsonify(build_openapi_spec(app))


def build_openapi_spec(app) -> dict[str, Any]:
    """Build an OpenAPI document from Flask routes and endpoint annotations."""
    spec = copy.deepcopy(BASE_SPEC)
    spec["servers"] = [{"url": request.url_root.rstrip("/"), "description": "Current server"}]

    for rule in sorted(app.url_map.iter_rules(), key=lambda item: item.rule):
        if _should_skip_rule(rule):
            continue

        endpoint_doc = ENDPOINT_DOCS.get(rule.endpoint)
        if not endpoint_doc:
            continue

        path = _flask_rule_to_openapi_path(rule.rule)
        path_item = spec["paths"].setdefault(path, {})

        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            operation = _build_operation(rule, method.lower(), endpoint_doc)
            path_item[method.lower()] = operation

    return spec


def _should_skip_rule(rule) -> bool:
    return (
        rule.endpoint == "static"
        or rule.rule.startswith(DOCS_ROUTE)
        or rule.endpoint.startswith("openapi_")
    )


def _build_operation(rule, method: str, endpoint_doc: dict[str, Any]) -> dict[str, Any]:
    operation = {
        "tags": endpoint_doc.get("tags", ["API"]),
        "summary": endpoint_doc["summary"],
        "description": endpoint_doc.get("description", endpoint_doc["summary"]),
        "operationId": f"{rule.endpoint.replace('.', '_')}_{method}",
        "parameters": _path_parameters(rule) + endpoint_doc.get("parameters", []),
        "responses": copy.deepcopy(endpoint_doc.get("responses", DEFAULT_RESPONSES)),
    }

    if endpoint_doc.get("requestBody"):
        operation["requestBody"] = copy.deepcopy(endpoint_doc["requestBody"])

    return operation


def _path_parameters(rule) -> list[dict[str, Any]]:
    parameters = []
    for name in sorted(rule.arguments):
        converter = rule._converters.get(name)
        parameters.append(
            {
                "name": name,
                "in": "path",
                "required": True,
                "description": PATH_PARAMETER_DESCRIPTIONS.get(
                    name, f"{name.replace('_', ' ').title()} path parameter."
                ),
                "schema": _schema_for_converter(converter),
            }
        )
    return parameters


def _schema_for_converter(converter) -> dict[str, Any]:
    converter_name = converter.__class__.__name__ if converter else ""
    if converter_name == "IntegerConverter":
        return {"type": "integer"}
    if converter_name == "FloatConverter":
        return {"type": "number", "format": "float"}
    return {"type": "string"}


def _flask_rule_to_openapi_path(rule: str) -> str:
    return _FLASK_PARAM_RE.sub(lambda match: "{" + match.group("name") + "}", rule)


def _swagger_ui_html(spec_url: str) -> str:
    spec_url_json = json.dumps(spec_url)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MiroFish API Docs</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body {{ margin: 0; background: #f7f8fa; }}
    .swagger-ui .topbar {{ display: none; }}
    .swagger-ui .info {{ margin: 32px 0; }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.addEventListener("load", function () {{
      SwaggerUIBundle({{
        url: {spec_url_json},
        dom_id: "#swagger-ui",
        deepLinking: true,
        displayRequestDuration: true,
        filter: true,
        tryItOutEnabled: true,
        presets: [SwaggerUIBundle.presets.apis],
        layout: "BaseLayout"
      }});
    }});
  </script>
</body>
</html>"""


def qp(
    name: str,
    description: str,
    schema_type: str = "string",
    *,
    required: bool = False,
    default: Any | None = None,
    enum: list[Any] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": schema_type}
    if default is not None:
        schema["default"] = default
    if enum:
        schema["enum"] = enum

    return {
        "name": name,
        "in": "query",
        "required": required,
        "description": description,
        "schema": schema,
    }


def json_body(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    example: dict[str, Any] | None = None,
    description: str = "JSON request body.",
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    body: dict[str, Any] = {
        "required": bool(required),
        "description": description,
        "content": {
            "application/json": {
                "schema": schema,
            }
        },
    }
    if example is not None:
        body["content"]["application/json"]["example"] = example
    return body


def multipart_body(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    description: str = "Multipart form request body.",
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    return {
        "required": bool(required),
        "description": description,
        "content": {
            "multipart/form-data": {
                "schema": schema,
            }
        },
    }


def doc(
    tag: str,
    summary: str,
    description: str,
    *,
    parameters: list[dict[str, Any]] | None = None,
    request_body: dict[str, Any] | None = None,
    responses: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "tags": [tag],
        "summary": summary,
        "description": description,
        "parameters": parameters or [],
        "responses": responses or DEFAULT_RESPONSES,
    }
    if request_body:
        result["requestBody"] = request_body
    return result


def success_response(description: str = "Successful response.") -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/SuccessEnvelope"}
            }
        },
    }


def error_response(description: str = "Error response.") -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
            }
        },
    }


def file_response(description: str = "File download response.") -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }


DEFAULT_RESPONSES = {
    "200": success_response(),
    "400": error_response("The request is invalid."),
    "404": error_response("The requested resource was not found."),
    "500": error_response("The server failed to process the request."),
}

FILE_DOWNLOAD_RESPONSES = {
    "200": file_response(),
    "400": error_response("The request is invalid."),
    "404": error_response("The requested file was not found."),
    "500": error_response("The server failed to process the request."),
}

PATH_PARAMETER_DESCRIPTIONS = {
    "agent_id": "Numeric agent identifier.",
    "entity_type": "Entity type label to filter by.",
    "entity_uuid": "Unique entity identifier inside the graph.",
    "graph_id": "Knowledge graph identifier.",
    "project_id": "Project identifier.",
    "report_id": "Report identifier.",
    "script_name": "Simulation helper script filename.",
    "section_index": "One-based report section index.",
    "simulation_id": "Simulation identifier.",
    "task_id": "Background task identifier.",
}

STRING = {"type": "string"}
BOOLEAN = {"type": "boolean"}
INTEGER = {"type": "integer"}
OBJECT = {"type": "object", "additionalProperties": True}
STRING_ARRAY = {"type": "array", "items": {"type": "string"}}

PROJECT_ID = {"type": "string", "example": "proj_123456789abc"}
GRAPH_ID = {"type": "string", "example": "mirofish_123456789abc"}
SIMULATION_ID = {"type": "string", "example": "sim_123456789abc"}
REPORT_ID = {"type": "string", "example": "report_123456789abc"}
TASK_ID = {"type": "string", "example": "task_123456789abc"}
PLATFORM = {"type": "string", "enum": ["twitter", "reddit", "parallel"]}

TASK_LOOKUP_BODY = json_body(
    {
        "task_id": TASK_ID,
        "simulation_id": SIMULATION_ID,
    },
    example={"task_id": "task_123456789abc", "simulation_id": "sim_123456789abc"},
    description="Provide either a task ID or the related simulation ID.",
)

SIMULATION_ID_BODY = json_body(
    {"simulation_id": SIMULATION_ID},
    required=["simulation_id"],
    example={"simulation_id": "sim_123456789abc"},
)


ENDPOINT_DOCS: dict[str, dict[str, Any]] = {
    "health": doc(
        "System",
        "Health check",
        "Returns a lightweight status response for load balancers and runtime checks.",
        responses={"200": success_response("Backend health status.")},
    ),
    "graph.get_project": doc(
        "Projects",
        "Get project details",
        "Returns persisted project metadata, ontology, graph status, uploaded files, and any error state.",
    ),
    "graph.list_projects": doc(
        "Projects",
        "List projects",
        "Lists recently created projects sorted by creation time.",
        parameters=[qp("limit", "Maximum number of projects to return.", "integer", default=50)],
    ),
    "graph.delete_project": doc(
        "Projects",
        "Delete project",
        "Deletes a project and its stored files from the backend.",
    ),
    "graph.reset_project": doc(
        "Projects",
        "Reset project state",
        "Resets graph-related project state so the graph can be rebuilt from the generated ontology.",
    ),
    "graph.generate_ontology": doc(
        "Graph",
        "Generate ontology from uploaded files",
        "Uploads source documents, extracts text, and uses the configured LLM to generate entity and edge types for the project.",
        request_body=multipart_body(
            {
                "files": {
                    "type": "array",
                    "items": {"type": "string", "format": "binary"},
                    "description": "One or more PDF, Markdown, or text files.",
                },
                "simulation_requirement": {
                    "type": "string",
                    "description": "Natural-language simulation requirement.",
                },
                "project_name": {
                    "type": "string",
                    "description": "Optional project display name.",
                },
                "additional_context": {
                    "type": "string",
                    "description": "Optional additional notes for ontology generation.",
                },
            },
            required=["files", "simulation_requirement"],
        ),
    ),
    "graph.build_graph": doc(
        "Graph",
        "Build graph",
        "Starts an asynchronous graph build task for a project that already has a generated ontology.",
        request_body=json_body(
            {
                "project_id": PROJECT_ID,
                "graph_name": {"type": "string", "example": "MiroFish Graph"},
                "chunk_size": {"type": "integer", "default": 500},
                "chunk_overlap": {"type": "integer", "default": 50},
                "force": {"type": "boolean", "default": False},
            },
            required=["project_id"],
            example={"project_id": "proj_123456789abc", "graph_name": "Market Outlook Graph"},
        ),
    ),
    "graph.get_task": doc(
        "Tasks",
        "Get task status",
        "Returns the latest state for a background task.",
    ),
    "graph.list_tasks": doc(
        "Tasks",
        "List tasks",
        "Lists persisted background tasks.",
    ),
    "graph.get_graph_data": doc(
        "Graph",
        "Get graph data",
        "Returns graph nodes and edges, optionally bypassing the cache.",
        parameters=[qp("refresh", "Refresh graph data instead of using the cache.", "boolean", default=False)],
    ),
    "graph.delete_graph": doc(
        "Graph",
        "Delete graph",
        "Deletes a graph from the configured graph backend.",
    ),
    "simulation.get_graph_entities": doc(
        "Entities",
        "List graph entities",
        "Returns filtered graph entities and optional edge context.",
        parameters=[
            qp("entity_types", "Comma-separated entity type names to include."),
            qp("enrich", "Include related edge information.", "boolean", default=True),
        ],
    ),
    "simulation.get_entity_detail": doc(
        "Entities",
        "Get entity details",
        "Returns a single graph entity with surrounding context.",
    ),
    "simulation.get_entities_by_type": doc(
        "Entities",
        "List entities by type",
        "Returns all graph entities that match a specific entity type.",
        parameters=[qp("enrich", "Include related edge information.", "boolean", default=True)],
    ),
    "simulation.create_simulation": doc(
        "Simulations",
        "Create simulation",
        "Creates a simulation record for a project and graph.",
        request_body=json_body(
            {
                "project_id": PROJECT_ID,
                "graph_id": GRAPH_ID,
                "enable_twitter": {"type": "boolean", "default": True},
                "enable_reddit": {"type": "boolean", "default": True},
            },
            required=["project_id"],
            example={"project_id": "proj_123456789abc", "enable_twitter": True, "enable_reddit": True},
        ),
    ),
    "simulation.prepare_simulation": doc(
        "Simulations",
        "Prepare simulation",
        "Starts an asynchronous preparation task that reads graph entities, generates OASIS profiles, and writes simulation configuration files.",
        request_body=json_body(
            {
                "simulation_id": SIMULATION_ID,
                "entity_types": STRING_ARRAY,
                "use_llm_for_profiles": {"type": "boolean", "default": True},
                "parallel_profile_count": {"type": "integer", "default": 5},
                "force_regenerate": {"type": "boolean", "default": False},
            },
            required=["simulation_id"],
            example={"simulation_id": "sim_123456789abc", "entity_types": ["Student"], "use_llm_for_profiles": True},
        ),
    ),
    "simulation.get_prepare_status": doc(
        "Simulations",
        "Get preparation status",
        "Returns the progress of a preparation task or the readiness state of a simulation.",
        request_body=TASK_LOOKUP_BODY,
    ),
    "simulation.get_simulation": doc(
        "Simulations",
        "Get simulation",
        "Returns simulation state and run instructions when the simulation is ready.",
    ),
    "simulation.list_simulations": doc(
        "Simulations",
        "List simulations",
        "Lists simulations, optionally filtered by project.",
        parameters=[qp("project_id", "Only return simulations for this project.")],
    ),
    "simulation.get_simulation_history": doc(
        "Simulations",
        "Get simulation history",
        "Returns an enriched history list combining simulations, projects, run status, and report references.",
        parameters=[qp("limit", "Maximum number of history entries to return.", "integer", default=20)],
    ),
    "simulation.get_simulation_profiles": doc(
        "Profiles",
        "Get simulation profiles",
        "Returns generated agent profiles for a simulation platform.",
        parameters=[qp("platform", "Profile platform.", enum=["reddit", "twitter"], default="reddit")],
    ),
    "simulation.get_simulation_profiles_realtime": doc(
        "Profiles",
        "Get profiles in real time",
        "Reads profile files directly so clients can watch generation progress before preparation finishes.",
        parameters=[qp("platform", "Profile platform.", enum=["reddit", "twitter"], default="reddit")],
    ),
    "simulation.get_simulation_config_realtime": doc(
        "Simulations",
        "Get simulation config in real time",
        "Reads the simulation configuration file directly and includes generation metadata.",
    ),
    "simulation.get_simulation_config": doc(
        "Simulations",
        "Get simulation config",
        "Returns the complete generated simulation configuration.",
    ),
    "simulation.download_simulation_config": doc(
        "Simulations",
        "Download simulation config",
        "Downloads the generated simulation_config.json file.",
        responses=FILE_DOWNLOAD_RESPONSES,
    ),
    "simulation.download_simulation_script": doc(
        "Simulations",
        "Download simulation script",
        "Downloads one of the supported helper scripts used to run OASIS simulations.",
        responses=FILE_DOWNLOAD_RESPONSES,
    ),
    "simulation.generate_profiles": doc(
        "Profiles",
        "Generate profiles from graph",
        "Generates OASIS agent profiles directly from graph entities without creating a simulation.",
        request_body=json_body(
            {
                "graph_id": GRAPH_ID,
                "entity_types": STRING_ARRAY,
                "use_llm": {"type": "boolean", "default": True},
                "platform": {"type": "string", "enum": ["reddit", "twitter", "raw"], "default": "reddit"},
            },
            required=["graph_id"],
            example={"graph_id": "mirofish_123456789abc", "entity_types": ["Student"], "platform": "reddit"},
        ),
    ),
    "simulation.start_simulation": doc(
        "Simulation Runs",
        "Start simulation run",
        "Starts or restarts a simulation runner process for Twitter, Reddit, or both platforms.",
        request_body=json_body(
            {
                "simulation_id": SIMULATION_ID,
                "platform": PLATFORM,
                "max_rounds": {"type": "integer", "minimum": 1},
                "enable_graph_memory_update": {"type": "boolean", "default": False},
                "force": {"type": "boolean", "default": False},
            },
            required=["simulation_id"],
            example={"simulation_id": "sim_123456789abc", "platform": "parallel", "max_rounds": 100},
        ),
    ),
    "simulation.stop_simulation": doc(
        "Simulation Runs",
        "Stop simulation run",
        "Stops the active simulation runner process.",
        request_body=SIMULATION_ID_BODY,
    ),
    "simulation.get_run_status": doc(
        "Simulation Runs",
        "Get run status",
        "Returns live runner status and progress counters for a simulation.",
    ),
    "simulation.get_run_status_detail": doc(
        "Simulation Runs",
        "Get detailed run status",
        "Returns runner status plus action lists split by platform.",
        parameters=[qp("platform", "Filter actions by platform.", enum=["twitter", "reddit"])],
    ),
    "simulation.get_simulation_actions": doc(
        "Simulation Data",
        "List simulation actions",
        "Returns stored agent actions with pagination and filters.",
        parameters=[
            qp("limit", "Maximum number of actions to return.", "integer", default=100),
            qp("offset", "Pagination offset.", "integer", default=0),
            qp("platform", "Filter by platform.", enum=["twitter", "reddit"]),
            qp("agent_id", "Filter by agent ID.", "integer"),
            qp("round_num", "Filter by simulation round.", "integer"),
        ],
    ),
    "simulation.get_simulation_timeline": doc(
        "Simulation Data",
        "Get simulation timeline",
        "Returns round-level timeline information for a simulation.",
        parameters=[
            qp("start_round", "First round to include.", "integer", default=0),
            qp("end_round", "Last round to include.", "integer"),
        ],
    ),
    "simulation.get_agent_stats": doc(
        "Simulation Data",
        "Get agent statistics",
        "Returns per-agent action and activity statistics.",
    ),
    "simulation.get_simulation_posts": doc(
        "Simulation Data",
        "List simulation posts",
        "Returns posts stored in the platform simulation database.",
        parameters=[
            qp("platform", "Database platform.", enum=["twitter", "reddit"], default="reddit"),
            qp("limit", "Maximum number of posts to return.", "integer", default=50),
            qp("offset", "Pagination offset.", "integer", default=0),
        ],
    ),
    "simulation.get_simulation_comments": doc(
        "Simulation Data",
        "List simulation comments",
        "Returns Reddit comments stored in the simulation database.",
        parameters=[
            qp("post_id", "Filter comments by post ID."),
            qp("limit", "Maximum number of comments to return.", "integer", default=50),
            qp("offset", "Pagination offset.", "integer", default=0),
        ],
    ),
    "simulation.interview_agent": doc(
        "Interviews",
        "Interview one agent",
        "Sends one interview prompt to a single agent in a running simulation environment.",
        request_body=json_body(
            {
                "simulation_id": SIMULATION_ID,
                "agent_id": INTEGER,
                "prompt": {"type": "string", "example": "What do you think about this event?"},
                "platform": {"type": "string", "enum": ["twitter", "reddit"]},
                "timeout": {"type": "integer", "default": 60},
            },
            required=["simulation_id", "agent_id", "prompt"],
            example={"simulation_id": "sim_123456789abc", "agent_id": 0, "prompt": "What do you think about this event?"},
        ),
    ),
    "simulation.interview_agents_batch": doc(
        "Interviews",
        "Interview multiple agents",
        "Sends multiple interview prompts to selected agents in one request.",
        request_body=json_body(
            {
                "simulation_id": SIMULATION_ID,
                "interviews": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_id": INTEGER,
                            "prompt": STRING,
                            "platform": {"type": "string", "enum": ["twitter", "reddit"]},
                        },
                        "required": ["agent_id", "prompt"],
                    },
                },
                "platform": {"type": "string", "enum": ["twitter", "reddit"]},
                "timeout": {"type": "integer", "default": 120},
            },
            required=["simulation_id", "interviews"],
            example={
                "simulation_id": "sim_123456789abc",
                "interviews": [{"agent_id": 0, "prompt": "What changed your opinion?"}],
            },
        ),
    ),
    "simulation.interview_all_agents": doc(
        "Interviews",
        "Interview all agents",
        "Sends the same interview prompt to every agent in the running simulation environment.",
        request_body=json_body(
            {
                "simulation_id": SIMULATION_ID,
                "prompt": {"type": "string", "example": "What is your overall opinion?"},
                "platform": {"type": "string", "enum": ["twitter", "reddit"]},
                "timeout": {"type": "integer", "default": 180},
            },
            required=["simulation_id", "prompt"],
            example={"simulation_id": "sim_123456789abc", "prompt": "What is your overall opinion?"},
        ),
    ),
    "simulation.get_interview_history": doc(
        "Interviews",
        "Get interview history",
        "Returns recorded interview responses for a simulation, with optional platform and agent filters.",
        request_body=json_body(
            {
                "simulation_id": SIMULATION_ID,
                "platform": {"type": "string", "enum": ["twitter", "reddit"]},
                "agent_id": INTEGER,
                "limit": {"type": "integer", "default": 100},
            },
            required=["simulation_id"],
            example={"simulation_id": "sim_123456789abc", "platform": "reddit", "limit": 100},
        ),
    ),
    "simulation.get_env_status": doc(
        "Interviews",
        "Get simulation environment status",
        "Checks whether the simulation environment can receive interview commands.",
        request_body=SIMULATION_ID_BODY,
    ),
    "simulation.close_simulation_env": doc(
        "Interviews",
        "Close simulation environment",
        "Sends a graceful shutdown command to the simulation command environment.",
        request_body=json_body(
            {
                "simulation_id": SIMULATION_ID,
                "timeout": {"type": "integer", "default": 30},
            },
            required=["simulation_id"],
            example={"simulation_id": "sim_123456789abc", "timeout": 30},
        ),
    ),
    "report.generate_report": doc(
        "Reports",
        "Generate report",
        "Starts an asynchronous report generation task for a simulation.",
        request_body=json_body(
            {
                "simulation_id": SIMULATION_ID,
                "force_regenerate": {"type": "boolean", "default": False},
            },
            required=["simulation_id"],
            example={"simulation_id": "sim_123456789abc", "force_regenerate": False},
        ),
    ),
    "report.get_generate_status": doc(
        "Reports",
        "Get report generation status",
        "Returns report generation task progress or the existing completed report for a simulation.",
        request_body=TASK_LOOKUP_BODY,
    ),
    "report.get_report": doc(
        "Reports",
        "Get report",
        "Returns report metadata, outline, markdown content, and status.",
    ),
    "report.get_report_by_simulation": doc(
        "Reports",
        "Get report by simulation",
        "Returns the report associated with a simulation.",
    ),
    "report.list_reports": doc(
        "Reports",
        "List reports",
        "Lists reports, optionally filtered by simulation.",
        parameters=[
            qp("simulation_id", "Only return reports for this simulation."),
            qp("limit", "Maximum number of reports to return.", "integer", default=50),
        ],
    ),
    "report.download_report": doc(
        "Reports",
        "Download report",
        "Downloads the report as a Markdown file.",
        responses=FILE_DOWNLOAD_RESPONSES,
    ),
    "report.delete_report": doc(
        "Reports",
        "Delete report",
        "Deletes a generated report and its stored artifacts.",
    ),
    "report.chat_with_report_agent": doc(
        "Reports",
        "Chat with report agent",
        "Asks the report agent a question. The agent may call graph search tools while answering.",
        request_body=json_body(
            {
                "simulation_id": SIMULATION_ID,
                "message": {"type": "string", "example": "Explain the main public opinion trend."},
                "chat_history": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"role": STRING, "content": STRING},
                    },
                },
            },
            required=["simulation_id", "message"],
            example={"simulation_id": "sim_123456789abc", "message": "Explain the main public opinion trend."},
        ),
    ),
    "report.get_report_progress": doc(
        "Reports",
        "Get report progress",
        "Returns real-time section generation progress for a report.",
    ),
    "report.get_report_sections": doc(
        "Reports",
        "List report sections",
        "Returns generated report sections before or after the full report is complete.",
    ),
    "report.get_single_section": doc(
        "Reports",
        "Get report section",
        "Returns the Markdown content for a single report section.",
    ),
    "report.check_report_status": doc(
        "Reports",
        "Check report status",
        "Checks whether a simulation has a report and whether report-based interview features are unlocked.",
    ),
    "report.get_agent_log": doc(
        "Report Logs",
        "Get report agent log",
        "Returns structured report-agent log entries, optionally starting from a specific line.",
        parameters=[qp("from_line", "Line offset for incremental log reads.", "integer", default=0)],
    ),
    "report.stream_agent_log": doc(
        "Report Logs",
        "Get full report agent log",
        "Returns the complete structured report-agent log in one response.",
    ),
    "report.get_console_log": doc(
        "Report Logs",
        "Get report console log",
        "Returns console-style report generation logs, optionally starting from a specific line.",
        parameters=[qp("from_line", "Line offset for incremental log reads.", "integer", default=0)],
    ),
    "report.stream_console_log": doc(
        "Report Logs",
        "Get full report console log",
        "Returns the complete console-style report generation log in one response.",
    ),
    "report.search_graph_tool": doc(
        "Report Tools",
        "Search graph",
        "Debug endpoint for running the report agent graph search tool directly.",
        request_body=json_body(
            {
                "graph_id": GRAPH_ID,
                "query": {"type": "string", "example": "Find relevant attitudes toward the policy."},
                "limit": {"type": "integer", "default": 10},
            },
            required=["graph_id", "query"],
            example={"graph_id": "mirofish_123456789abc", "query": "public opinion trend", "limit": 10},
        ),
    ),
    "report.get_graph_statistics_tool": doc(
        "Report Tools",
        "Get graph statistics",
        "Debug endpoint for running the report agent graph statistics tool directly.",
        request_body=json_body(
            {"graph_id": GRAPH_ID},
            required=["graph_id"],
            example={"graph_id": "mirofish_123456789abc"},
        ),
    ),
}


BASE_SPEC: dict[str, Any] = {
    "openapi": OPENAPI_VERSION,
    "info": {
        "title": "MiroFish Backend API",
        "version": "0.1.0",
        "description": (
            "OpenAPI documentation for the MiroFish backend. "
            "The API manages projects, graph construction, simulations, reports, and agent interviews."
        ),
        "license": {"name": "AGPL-3.0"},
    },
    "tags": [
        {"name": "System", "description": "Runtime and health endpoints."},
        {"name": "Projects", "description": "Project lifecycle and persisted project metadata."},
        {"name": "Graph", "description": "Ontology generation, graph build tasks, and graph data."},
        {"name": "Tasks", "description": "Long-running task status endpoints."},
        {"name": "Entities", "description": "Graph entity retrieval endpoints."},
        {"name": "Simulations", "description": "Simulation creation, preparation, history, and configuration."},
        {"name": "Profiles", "description": "Generated OASIS agent profiles."},
        {"name": "Simulation Runs", "description": "Simulation runner process control and live status."},
        {"name": "Simulation Data", "description": "Posts, comments, actions, timelines, and statistics."},
        {"name": "Interviews", "description": "Interactive interviews with simulated agents."},
        {"name": "Reports", "description": "Report generation, retrieval, and agent chat."},
        {"name": "Report Logs", "description": "Structured and console report generation logs."},
        {"name": "Report Tools", "description": "Debug endpoints for report-agent graph tools."},
    ],
    "paths": {},
    "components": {
        "schemas": {
            "SuccessEnvelope": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "example": True},
                    "data": {"description": "Endpoint-specific response payload."},
                    "message": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["success"],
            },
            "ErrorEnvelope": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "example": False},
                    "error": {"type": "string", "example": "Resource not found"},
                    "traceback": {"type": "string", "description": "Debug traceback returned by some error paths."},
                    "task_id": {"type": "string", "description": "Related task ID when available."},
                },
                "required": ["success", "error"],
            },
            "Project": {
                "type": "object",
                "properties": {
                    "project_id": PROJECT_ID,
                    "name": STRING,
                    "status": {
                        "type": "string",
                        "enum": [
                            "created",
                            "ontology_generated",
                            "graph_building",
                            "graph_completed",
                            "failed",
                        ],
                    },
                    "created_at": {"type": "string", "format": "date-time"},
                    "updated_at": {"type": "string", "format": "date-time"},
                    "files": {"type": "array", "items": OBJECT},
                    "total_text_length": INTEGER,
                    "ontology": OBJECT,
                    "analysis_summary": STRING,
                    "graph_id": GRAPH_ID,
                    "graph_build_task_id": TASK_ID,
                    "simulation_requirement": STRING,
                    "chunk_size": INTEGER,
                    "chunk_overlap": INTEGER,
                    "error": STRING,
                },
            },
            "Task": {
                "type": "object",
                "properties": {
                    "task_id": TASK_ID,
                    "task_type": STRING,
                    "status": {"type": "string", "enum": ["pending", "processing", "completed", "failed"]},
                    "created_at": {"type": "string", "format": "date-time"},
                    "updated_at": {"type": "string", "format": "date-time"},
                    "progress": {"type": "integer", "minimum": 0, "maximum": 100},
                    "message": STRING,
                    "progress_detail": OBJECT,
                    "result": OBJECT,
                    "error": STRING,
                    "metadata": OBJECT,
                },
            },
            "Simulation": {
                "type": "object",
                "properties": {
                    "simulation_id": SIMULATION_ID,
                    "project_id": PROJECT_ID,
                    "graph_id": GRAPH_ID,
                    "status": STRING,
                    "entities_count": INTEGER,
                    "profiles_count": INTEGER,
                    "entity_types": STRING_ARRAY,
                    "created_at": {"type": "string", "format": "date-time"},
                    "updated_at": {"type": "string", "format": "date-time"},
                },
            },
            "Report": {
                "type": "object",
                "properties": {
                    "report_id": REPORT_ID,
                    "simulation_id": SIMULATION_ID,
                    "status": STRING,
                    "outline": OBJECT,
                    "markdown_content": STRING,
                    "created_at": {"type": "string", "format": "date-time"},
                    "completed_at": {"type": "string", "format": "date-time"},
                    "error": STRING,
                },
            },
            "GraphData": {
                "type": "object",
                "properties": {
                    "nodes": {"type": "array", "items": OBJECT},
                    "edges": {"type": "array", "items": OBJECT},
                    "node_count": INTEGER,
                    "edge_count": INTEGER,
                },
            },
            "RunState": {
                "type": "object",
                "properties": {
                    "simulation_id": SIMULATION_ID,
                    "runner_status": STRING,
                    "current_round": INTEGER,
                    "total_rounds": INTEGER,
                    "progress_percent": {"type": "number"},
                    "twitter_running": BOOLEAN,
                    "reddit_running": BOOLEAN,
                    "total_actions_count": INTEGER,
                    "started_at": {"type": "string", "format": "date-time"},
                    "updated_at": {"type": "string", "format": "date-time"},
                },
            },
        }
    },
}
