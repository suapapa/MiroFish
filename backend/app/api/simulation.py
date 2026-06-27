"""
    Simulate related API routing
    Step2: Zep entity reading and filtering, OASIS simulation preparation and running (full automation)
"""

import os
import traceback
from typing import Optional
from flask import request, jsonify, send_file

from . import simulation_bp
from ..config import Config
from ..services.zep_entity_reader import ZepEntityReader
from ..services.oasis_profile_generator import OasisProfileGenerator
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale
from ..utils.prompts import get_prompt
from ..models.project import ProjectManager

logger = get_logger('mirofish.api.simulation')


# Interview prompt optimization prefix
# Adding this prefix can avoid the Agent calling the tool and reply directly with text.
INTERVIEW_PROMPT_PREFIX = get_prompt("interview.optimize_prefix")


def optimize_interview_prompt(prompt: str) -> str:
    """
        Optimize Interview questions and add prefixes to avoid Agent calling tools

            Args:
                prompt: original question

            Returns:
                Optimized questions
    """
    if not prompt:
        return prompt
    # Avoid adding prefixes repeatedly
    if prompt.startswith(INTERVIEW_PROMPT_PREFIX):
        return prompt
    return f"{INTERVIEW_PROMPT_PREFIX}{prompt}"


# ============== Entity reading interface ==============

@simulation_bp.route('/entities/<graph_id>', methods=['GET'])
def get_graph_entities(graph_id: str):
    """
        Get all entities in the graph (filtered)

            Only return nodes that match predefined entity types (Labels are not just Entity nodes)

            Query parameters:
                entity_types: comma separated list of entity types (optional, for further filtering)
                enrich: whether to obtain relevant edge information (default true)
    """
    try:
        entity_types_str = request.args.get('entity_types', '')
        entity_types = [t.strip() for t in entity_types_str.split(',') if t.strip()] if entity_types_str else None
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        logger.info(f"Fetching graph entities: graph_id={graph_id}, entity_types={entity_types}, enrich={enrich}")
        
        reader = ZepEntityReader()
        result = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Failed to fetch graph entities: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/<entity_uuid>', methods=['GET'])
def get_entity_detail(graph_id: str, entity_uuid: str):
    """Get details of a single entity"""
    try:
        reader = ZepEntityReader()
        entity = reader.get_entity_with_context(graph_id, entity_uuid)
        
        if not entity:
            return jsonify({
                "success": False,
                "error": t('api.entityNotFound', id=entity_uuid)
            }), 404
        
        return jsonify({
            "success": True,
            "data": entity.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Failed to fetch entity detail: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/by-type/<entity_type>', methods=['GET'])
def get_entities_by_type(graph_id: str, entity_type: str):
    """Get all entities of the specified type"""
    try:
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        reader = ZepEntityReader()
        entities = reader.get_entities_by_type(
            graph_id=graph_id,
            entity_type=entity_type,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "count": len(entities),
                "entities": [e.to_dict() for e in entities]
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to fetch entities: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Simulation management interface ==============

@simulation_bp.route('/create', methods=['POST'])
def create_simulation():
    """
        Create a new simulation

            Note: parameters such as max_rounds are intelligently generated by LLM and do not need to be set manually.

            Request (JSON):
                {
                    "project_id": "proj_xxxx", // required
                    "graph_id": "mirofish_xxxx", // Optional, if not provided, it will be obtained from the project
                    "enable_twitter": true, // optional, default true
                    "enable_reddit": true // Optional, default true
                }

            Return:
                {
                    "success": true,
                    "data": {
                        "simulation_id": "sim_xxxx",
                        "project_id": "proj_xxxx",
                        "graph_id": "mirofish_xxxx",
                        "status": "created",
                        "enable_twitter": true,
                        "enable_reddit": true,
                        "created_at": "2025-12-01T10:00:00"
                    }
                }
    """
    try:
        data = request.get_json() or {}
        
        project_id = data.get('project_id')
        if not project_id:
            return jsonify({
                "success": False,
                "error": t('api.requireProjectId')
            }), 400
        
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=project_id)
            }), 404
        
        graph_id = data.get('graph_id') or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": t('api.graphNotBuilt')
            }), 400
        
        manager = SimulationManager()
        state = manager.create_simulation(
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=data.get('enable_twitter', True),
            enable_reddit=data.get('enable_reddit', True),
        )
        
        return jsonify({
            "success": True,
            "data": state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Failed to create simulation: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _check_simulation_prepared(simulation_id: str) -> tuple:
    """
        Check if the simulation is ready to complete

            Check conditions:
            1. state.json exists and status is "ready"
            2. Necessary files exist: reddit_profiles.json, twitter_profiles.csv, simulation_config.json

            Note: The run script (run_*.py) remains in the backend/scripts/ directory and is no longer copied to the simulation directory.

            Args:
                simulation_id: simulation ID

            Returns:
                (is_prepared: bool, info: dict)
    """
    import os
    from ..config import Config
    
    simulation_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
    
    # Check if directory exists
    if not os.path.exists(simulation_dir):
        return False, {"reason": "Simulation directory does not exist"}
    
    # List of necessary files (excluding scripts, which are located in backend/scripts/)
    required_files = [
        "state.json",
        "simulation_config.json",
        "reddit_profiles.json",
        "twitter_profiles.csv"
    ]
    
    # Check if the file exists
    existing_files = []
    missing_files = []
    for f in required_files:
        file_path = os.path.join(simulation_dir, f)
        if os.path.exists(file_path):
            existing_files.append(f)
        else:
            missing_files.append(f)
    
    if missing_files:
        return False, {
            "reason": "Missing required files",
            "missing_files": missing_files,
            "existing_files": existing_files
        }
    
    # Check the status in state.json
    state_file = os.path.join(simulation_dir, "state.json")
    try:
        import json
        with open(state_file, 'r', encoding='utf-8') as f:
            state_data = json.load(f)
        
        status = state_data.get("status", "")
        config_generated = state_data.get("config_generated", False)
        
        # Detailed log
        logger.debug(f"Checking simulation preparation status: {simulation_id}, status={status}, config_generated={config_generated}")
        
        # If config_generated=True and the file exists, the preparation is considered complete
        # The following statuses all indicate that the preparation work has been completed:
        # - ready: ready to run
        # - preparing: if config_generated=True indicates that it has been completed
        # - running: running, indicating that preparations have been completed long ago
        # - completed: The operation is completed, indicating that the preparation has been completed long ago
        # - stopped: has stopped, indicating that the preparation has been completed long ago
        # - failed: The operation failed (but preparation is complete)
        prepared_statuses = ["ready", "preparing", "running", "completed", "stopped", "failed"]
        if status in prepared_statuses and config_generated:
            # Get file statistics
            profiles_file = os.path.join(simulation_dir, "reddit_profiles.json")
            config_file = os.path.join(simulation_dir, "simulation_config.json")
            
            profiles_count = 0
            if os.path.exists(profiles_file):
                with open(profiles_file, 'r', encoding='utf-8') as f:
                    profiles_data = json.load(f)
                    profiles_count = len(profiles_data) if isinstance(profiles_data, list) else 0
            
            # If the status is preparing but the file has been completed, the status is automatically updated to ready.
            if status == "preparing":
                try:
                    state_data["status"] = "ready"
                    from datetime import datetime
                    state_data["updated_at"] = datetime.now().isoformat()
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump(state_data, f, ensure_ascii=False, indent=2)
                    logger.info(f"Auto-updated simulation status: {simulation_id} preparing -> ready")
                    status = "ready"
                except Exception as e:
                    logger.warning(f"Failed to auto-update status: {e}")
            
            logger.info(f"Simulation {simulation_id} check result: preparation complete (status={status}, config_generated={config_generated})")
            return True, {
                "status": status,
                "entities_count": state_data.get("entities_count", 0),
                "profiles_count": profiles_count,
                "entity_types": state_data.get("entity_types", []),
                "config_generated": config_generated,
                "created_at": state_data.get("created_at"),
                "updated_at": state_data.get("updated_at"),
                "existing_files": existing_files
            }
        else:
            logger.warning(f"Simulation {simulation_id} check result: preparation not complete (status={status}, config_generated={config_generated})")
            return False, {
                "reason": f"Status not in prepared list or config_generated is false: status={status}, config_generated={config_generated}",
                "status": status,
                "config_generated": config_generated
            }
            
    except Exception as e:
        return False, {"reason": f"Failed to read state file: {str(e)}"}


def _find_active_prepare_task(simulation_id: str, prepare_task_id: Optional[str] = None):
    """Finds prepare tasks that are still in progress for the specified simulation."""
    from ..models.task import TaskManager, TaskStatus

    task_manager = TaskManager()
    active_task = task_manager.find_active_task_for_simulation(
        simulation_id, "simulation_prepare"
    )
    if active_task:
        return active_task

    if prepare_task_id:
        task = task_manager.get_task(prepare_task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING):
            return task

    return None


@simulation_bp.route('/prepare', methods=['POST'])
def prepare_simulation():
    """
        Prepare the simulation environment (asynchronous tasks, LLM intelligently generates all parameters)

            This is a time-consuming operation, and the interface will return task_id immediately.
            Use GET /api/simulation/prepare/status to query the progress

            Features:
            - Automatically detect completed preparations to avoid repeated generation
            - If the preparation is completed, return the existing results directly
            - Support force regeneration (force_regenerate=true)

            Steps:
            1. Check whether the preparations have been completed
            2. Read and filter entities from the Zep map
            3. Generate OASIS Agent Profile for each entity (with retry mechanism)
            4. LLM intelligently generates simulation configuration (with retry mechanism)
            5. Save configuration files and preset scripts

            Request (JSON):
                {
                    "simulation_id": "sim_xxxx", // required, simulation ID
                    "entity_types": ["Student", "PublicFigure"], // Optional, specify the entity type
                    "use_llm_for_profiles": true, // Optional, whether to use LLM to generate profiles
                    "parallel_profile_count": 5, // Optional, the number of profiles generated in parallel, the default is 5
                    "force_regenerate": false // Optional, force regeneration, default false
                }

            Return:
                {
                    "success": true,
                    "data": {
                        "simulation_id": "sim_xxxx",
                        "task_id": "task_xxxx", // Returned when new task
                        "status": "preparing|ready",
                        "message": "The preparation task has been started|The preparation work has been completed",
                        "already_prepared": true|false // Whether it is ready
                    }
                }
    """
    import threading
    from ..models.task import TaskManager, TaskStatus
    from ..config import Config
    
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        # Check whether to force a regeneration
        force_regenerate = data.get('force_regenerate', False)
        logger.info(f"Handling /prepare request: simulation_id={simulation_id}, force_regenerate={force_regenerate}")
        
        # Check whether it is ready (to avoid repeated generation)
        if not force_regenerate:
            logger.debug(f"Checking whether simulation {simulation_id} is already prepared...")
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            logger.debug(f"Check result: is_prepared={is_prepared}, prepare_info={prepare_info}")
            if is_prepared:
                logger.info(f"Simulation {simulation_id} already prepared, skipping regeneration")
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "message": t('api.alreadyPrepared'),
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
            else:
                logger.info(f"Simulation {simulation_id} not prepared, starting preparation task")

            # If it is already in preparation, return to the existing task (to avoid triggering the full map read again)
            if state.status == SimulationStatus.PREPARING:
                existing_task = _find_active_prepare_task(
                    simulation_id, state.prepare_task_id
                )
                if existing_task:
                    logger.info(
                        f"Simulation {simulation_id} already preparing, "
                        f"returning existing task {existing_task.task_id}"
                    )
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "task_id": existing_task.task_id,
                            "status": "preparing",
                            "message": t('api.prepareInProgress'),
                            "already_prepared": False,
                            "resumed": True,
                            "expected_entities_count": state.entities_count or None,
                            "entity_types": state.entity_types,
                        }
                    })
                logger.warning(
                    f"Simulation {simulation_id} status is preparing but no active task found, "
                    "starting a new preparation task"
                )
        
        # Get necessary information from the project
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=state.project_id)
            }), 404
        
        # Get simulation requirements
        simulation_requirement = project.simulation_requirement or ""
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": t('api.projectMissingRequirement')
            }), 400
        
        # Get document text
        document_text = ProjectManager.get_extracted_text(state.project_id) or ""
        
        entity_types_list = data.get('entity_types')
        use_llm_for_profiles = data.get('use_llm_for_profiles', True)
        parallel_profile_count = data.get('parallel_profile_count', 5)
        
        # Create an asynchronous task (returns task_id immediately; entity reading occurs in the background)
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="simulation_prepare",
            metadata={
                "simulation_id": simulation_id,
                "project_id": state.project_id
            }
        )
        
        # Update simulation status and record task_id for resuming polling after refresh
        state.status = SimulationStatus.PREPARING
        state.prepare_task_id = task_id
        state.error = None
        manager._save_simulation_state(state)
        
        # Capture locale before spawning background thread
        current_locale = get_locale()

        # Define background tasks
        def run_prepare():
            set_locale(current_locale)
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message=t('progress.startPreparingEnv')
                )
                
                # Prepare for simulation (with progress callback)
                # Storage phase progress details
                stage_details = {}
                
                def progress_callback(stage, progress, message, **kwargs):
                    # Calculate total progress
                    stage_weights = {
                        "reading": (0, 20),           # 0-20%
                        "generating_profiles": (20, 70),  # 20-70%
                        "generating_config": (70, 90),    # 70-90%
                        "copying_scripts": (90, 100)       # 90-100%
                    }
                    
                    start, end = stage_weights.get(stage, (0, 100))
                    current_progress = int(start + (end - start) * progress / 100)
                    
                    # Build detailed progress information
                    stage_names = {
                        "reading": t('progress.readingGraphEntities'),
                        "generating_profiles": t('progress.generatingProfiles'),
                        "generating_config": t('progress.generatingSimConfig'),
                        "copying_scripts": t('progress.preparingScripts')
                    }
                    
                    stage_index = list(stage_weights.keys()).index(stage) + 1 if stage in stage_weights else 1
                    total_stages = len(stage_weights)
                    
                    # Update phase details
                    stage_details[stage] = {
                        "stage_name": stage_names.get(stage, stage),
                        "stage_progress": progress,
                        "current": kwargs.get("current", 0),
                        "total": kwargs.get("total", 0),
                        "item_name": kwargs.get("item_name", "")
                    }
                    
                    # Build detailed progress information
                    detail = stage_details[stage]
                    progress_detail_data = {
                        "current_stage": stage,
                        "current_stage_name": stage_names.get(stage, stage),
                        "stage_index": stage_index,
                        "total_stages": total_stages,
                        "stage_progress": progress,
                        "current_item": detail["current"],
                        "total_items": detail["total"],
                        "item_description": message
                    }
                    
                    # Build concise messages
                    if detail["total"] > 0:
                        detailed_message = (
                            f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: "
                            f"{detail['current']}/{detail['total']} - {message}"
                        )
                    else:
                        detailed_message = f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: {message}"
                    
                    task_manager.update_task(
                        task_id,
                        progress=current_progress,
                        message=detailed_message,
                        progress_detail=progress_detail_data
                    )
                
                result_state = manager.prepare_simulation(
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement,
                    document_text=document_text,
                    defined_entity_types=entity_types_list,
                    use_llm_for_profiles=use_llm_for_profiles,
                    progress_callback=progress_callback,
                    parallel_profile_count=parallel_profile_count
                )
                
                # Mission accomplished
                task_manager.complete_task(
                    task_id,
                    result=result_state.to_simple_dict()
                )
                
            except Exception as e:
                logger.error(f"Failed to prepare simulation: {str(e)}")
                task_manager.fail_task(task_id, str(e))
                
                # Update simulation status to failed
                state = manager.get_simulation(simulation_id)
                if state:
                    state.status = SimulationStatus.FAILED
                    state.error = str(e)
                    manager._save_simulation_state(state)
        
        # Start background thread
        thread = threading.Thread(target=run_prepare, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "task_id": task_id,
                "status": "preparing",
                "message": t('api.prepareStarted'),
                "already_prepared": False,
                "expected_entities_count": state.entities_count or None,
                "entity_types": state.entity_types
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"Failed to start preparation task: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/prepare/status', methods=['POST'])
def get_prepare_status():
    """
        Query the progress of preparation tasks

            Two query methods are supported:
            1. Query the progress of the ongoing task through task_id
            2. Check whether the preparation work has been completed through simulation_id

            Request (JSON):
                {
                    "task_id": "task_xxxx", // Optional, task_id returned by prepare
                    "simulation_id": "sim_xxxx" // Optional, simulation ID (used to check completed preparation)
                }

            Return:
                {
                    "success": true,
                    "data": {
                        "task_id": "task_xxxx",
                        "status": "processing|completed|ready",
                        "progress": 45,
                        "message": "...",
                        "already_prepared": true|false, // Whether it is ready to be completed
                        "prepare_info": {...} // Detailed information when preparation is completed
                    }
                }
    """
    from ..models.task import TaskManager
    
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        manager = SimulationManager()
        
        # If simulation_id is provided, first check whether it is ready
        if simulation_id:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            if is_prepared:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "progress": 100,
                        "message": t('api.alreadyPrepared'),
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })

            # When task_id is not passed, try to resume from simulation state or active task
            if not task_id:
                state = manager.get_simulation(simulation_id)
                if state and state.prepare_task_id:
                    task_id = state.prepare_task_id

                if not task_id:
                    active_task = _find_active_prepare_task(
                        simulation_id,
                        state.prepare_task_id if state else None,
                    )
                    if active_task:
                        task_id = active_task.task_id
        
        # If there is no task_id, it returns not started or cannot be resumed.
        if not task_id:
            if simulation_id:
                state = manager.get_simulation(simulation_id)
                if state and state.status == SimulationStatus.PREPARING:
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "status": "preparing",
                            "progress": 0,
                            "message": t('api.prepareInProgress'),
                            "already_prepared": False,
                        }
                    })
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "not_started",
                        "progress": 0,
                        "message": t('api.notStartedPrepare'),
                        "already_prepared": False
                    }
                })
            return jsonify({
                "success": False,
                "error": t('api.requireTaskOrSimId')
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            # The task does not exist, but if there is a simulation_id, check whether it is ready to complete
            if simulation_id:
                is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
                if is_prepared:
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "task_id": task_id,
                            "status": "ready",
                            "progress": 100,
                            "message": t('api.taskCompletedPrepared'),
                            "already_prepared": True,
                            "prepare_info": prepare_info
                        }
                    })
            
            return jsonify({
                "success": False,
                "error": t('api.taskNotFound', id=task_id)
            }), 404
        
        task_dict = task.to_dict()
        task_dict["already_prepared"] = False
        
        return jsonify({
            "success": True,
            "data": task_dict
        })
        
    except Exception as e:
        logger.error(f"Failed to query task status: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@simulation_bp.route('/<simulation_id>', methods=['GET'])
def get_simulation(simulation_id: str):
    """Get simulation status"""
    try:
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        result = state.to_dict()
        
        # If the simulation is ready, append running instructions
        if state.status == SimulationStatus.READY:
            result["run_instructions"] = manager.get_run_instructions(simulation_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"Failed to get simulation status: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/list', methods=['GET'])
def list_simulations():
    """
        List all simulations

            Query parameters:
                project_id: filter by project ID (optional)
    """
    try:
        project_id = request.args.get('project_id')
        
        manager = SimulationManager()
        simulations = manager.list_simulations(project_id=project_id)
        
        return jsonify({
            "success": True,
            "data": [s.to_dict() for s in simulations],
            "count": len(simulations)
        })
        
    except Exception as e:
        logger.error(f"Failed to list simulations: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _enrich_project_only_entry(project) -> dict:
    """Build history entries for projects for which simulations have not yet been created"""
    status = project.status.value if hasattr(project.status, 'value') else project.status
    return {
        "simulation_id": None,
        "project_id": project.project_id,
        "project_name": project.name,
        "project_status": status,
        "simulation_requirement": project.simulation_requirement or "",
        "status": status,
        "graph_id": project.graph_id,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "current_round": 0,
        "total_rounds": 0,
        "runner_status": "idle",
        "report_id": None,
        "files": [
            {"filename": f.get("filename", "Unknown file")}
            for f in (project.files or [])[:3]
        ],
        "version": "v1.0.2",
        "created_date": (project.created_at or "")[:10],
    }


def _get_report_id_for_simulation(simulation_id: str) -> str:
    """
        Get the latest report_id corresponding to simulation

            Traverse the reports directory to find the report matching simulation_id,
            If there are multiple, return the latest (sorted by created_at)

            Args:
                simulation_id: simulation ID

            Returns:
                report_id or None
    """
    import json
    from datetime import datetime
    
    # reports directory path: backend/uploads/reports
    # __file__ is app/api/simulation.py, it needs to go up two levels to backend/
    reports_dir = os.path.join(os.path.dirname(__file__), '../../uploads/reports')
    if not os.path.exists(reports_dir):
        return None
    
    matching_reports = []
    
    try:
        for report_folder in os.listdir(reports_dir):
            report_path = os.path.join(reports_dir, report_folder)
            if not os.path.isdir(report_path):
                continue
            
            meta_file = os.path.join(report_path, "meta.json")
            if not os.path.exists(meta_file):
                continue
            
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                
                if meta.get("simulation_id") == simulation_id:
                    matching_reports.append({
                        "report_id": meta.get("report_id"),
                        "created_at": meta.get("created_at", ""),
                        "status": meta.get("status", "")
                    })
            except Exception:
                continue
        
        if not matching_reports:
            return None
        
        # Sort in reverse order of creation time and return the latest
        matching_reports.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return matching_reports[0].get("report_id")
        
    except Exception as e:
        logger.warning(f"Failed to find report for simulation {simulation_id}: {e}")
        return None


@simulation_bp.route('/history', methods=['GET'])
def get_simulation_history():
    """
        Get a list of historical simulations (with project details)

            Used to display historical projects on the home page and return a simulation list containing rich information such as project names and descriptions.

            Query parameters:
                limit: return quantity limit (default 20)

            Return:
                {
                    "success": true,
                    "data": [
                        {
                            "simulation_id": "sim_xxxx",
                            "project_id": "proj_xxxx",
                            "project_name": "Public Opinion Analysis of Wuhan University",
                            "simulation_requirement": "If Wuhan University releases...",
                            "status": "completed",
                            "entities_count": 68,
                            "profiles_count": 68,
                            "entity_types": ["Student", "Professor", ...],
                            "created_at": "2024-12-10",
                            "updated_at": "2024-12-10",
                            "total_rounds": 120,
                            "current_round": 120,
                            "report_id": "report_xxxx",
                            "version": "v1.0.2"
                        },
                        ...
                    ],
                    "count": 7
                }
    """
    try:
        limit = request.args.get('limit', 20, type=int)
        
        manager = SimulationManager()
        simulations = sorted(
            manager.list_simulations(),
            key=lambda s: s.created_at,
            reverse=True,
        )
        
        # Enhance simulation data and complement projects where simulations have not yet been created
        enriched_simulations = []
        simulated_project_ids = set()
        for sim in simulations:
            sim_dict = sim.to_dict()
            
            # Get simulation configuration information (read simulation_requirement from simulation_config.json)
            config = manager.get_simulation_config(sim.simulation_id)
            if config:
                sim_dict["simulation_requirement"] = config.get("simulation_requirement", "")
                time_config = config.get("time_config", {})
                sim_dict["total_simulation_hours"] = time_config.get("total_simulation_hours", 0)
                # Recommended number of rounds (backup value)
                recommended_rounds = int(
                    time_config.get("total_simulation_hours", 0) * 60 / 
                    max(time_config.get("minutes_per_round", 60), 1)
                )
            else:
                sim_dict["simulation_requirement"] = ""
                sim_dict["total_simulation_hours"] = 0
                recommended_rounds = 0
            
            # Get the running status (read the actual number of rounds set by the user from run_state.json)
            run_state = SimulationRunner.get_run_state(sim.simulation_id)
            if run_state:
                sim_dict["current_round"] = run_state.current_round
                sim_dict["runner_status"] = run_state.runner_status.value
                # Use the total_rounds set by the user, or use the recommended number of rounds if none
                sim_dict["total_rounds"] = run_state.total_rounds if run_state.total_rounds > 0 else recommended_rounds
            else:
                sim_dict["current_round"] = 0
                sim_dict["runner_status"] = "idle"
                sim_dict["total_rounds"] = recommended_rounds
            
            # Get the file list of associated projects (up to 3)
            project = ProjectManager.get_project(sim.project_id)
            if project and hasattr(project, 'files') and project.files:
                sim_dict["files"] = [
                    {"filename": f.get("filename", "Unknown file")} 
                    for f in project.files[:3]
                ]
            else:
                sim_dict["files"] = []
            
            # Get the associated report_id (find the latest report for this simulation)
            sim_dict["report_id"] = _get_report_id_for_simulation(sim.simulation_id)
            
            # Add version number
            sim_dict["version"] = "v1.0.2"
            
            # Format date
            try:
                created_date = sim_dict.get("created_at", "")[:10]
                sim_dict["created_date"] = created_date
            except:
                sim_dict["created_date"] = ""
            
            simulated_project_ids.add(sim.project_id)
            enriched_simulations.append(sim_dict)

        for project in ProjectManager.list_projects(limit=limit * 2):
            if project.project_id in simulated_project_ids:
                continue
            enriched_simulations.append(_enrich_project_only_entry(project))

        enriched_simulations.sort(
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )
        enriched_simulations = enriched_simulations[:limit]
        
        return jsonify({
            "success": True,
            "data": enriched_simulations,
            "count": len(enriched_simulations)
        })
        
    except Exception as e:
        logger.error(f"Failed to get simulation history: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles', methods=['GET'])
def get_simulation_profiles(simulation_id: str):
    """
        Get simulated Agent Profile

            Query parameters:
                platform: platform type (reddit/twitter, default reddit)
    """
    try:
        platform = request.args.get('platform', 'reddit')
        
        manager = SimulationManager()
        profiles = manager.get_profiles(simulation_id, platform=platform)
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "count": len(profiles),
                "profiles": profiles
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"Failed to get profiles: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles/realtime', methods=['GET'])
def get_simulation_profiles_realtime(simulation_id: str):
    """
        Obtain the simulated Agent Profile in real time (used to view the progress in real time during the generation process)

            Differences from the /profiles interface:
            - Read files directly without going through SimulationManager
            - Suitable for real-time viewing during the generation process
            - Return additional metadata (such as file modification time, whether it is being generated, etc.)

            Query parameters:
                platform: platform type (reddit/twitter, default reddit)

            Return:
                {
                    "success": true,
                    "data": {
                        "simulation_id": "sim_xxxx",
                        "platform": "reddit",
                        "count": 15,
                        "total_expected": 93, // expected total (if any)
                        "is_generating": true, // Whether it is generating
                        "file_exists": true,
                        "file_modified_at": "2025-12-04T18:20:00",
                        "profiles": [...]
                    }
                }
    """
    import json
    import csv
    from datetime import datetime
    
    try:
        platform = request.args.get('platform', 'reddit')
        
        # Get simulation directory
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        # Determine file path
        if platform == "reddit":
            profiles_file = os.path.join(sim_dir, "reddit_profiles.json")
        else:
            profiles_file = os.path.join(sim_dir, "twitter_profiles.csv")
        
        # Check if the file exists
        file_exists = os.path.exists(profiles_file)
        profiles = []
        file_modified_at = None
        
        if file_exists:
            # Get file modification time
            file_stat = os.stat(profiles_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                if platform == "reddit":
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        profiles = json.load(f)
                else:
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        profiles = list(reader)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to read profiles file (may be writing): {e}")
                profiles = []
        
        # Check whether it is being generated (judged by state.json)
        is_generating = False
        total_expected = None
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    total_expected = state_data.get("entities_count")
            except Exception:
                pass
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "platform": platform,
                "count": len(profiles),
                "total_expected": total_expected,
                "is_generating": is_generating,
                "file_exists": file_exists,
                "file_modified_at": file_modified_at,
                "profiles": profiles
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to get realtime profiles: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/realtime', methods=['GET'])
def get_simulation_config_realtime(simulation_id: str):
    """
        Get simulation configuration in real time (for viewing progress in real time during the build process)

            Differences from the /config interface:
            - Read files directly without going through SimulationManager
            - Suitable for real-time viewing during the generation process
            - Return additional metadata (such as file modification time, whether it is being generated, etc.)
            - Partial information can be returned even if the configuration has not been generated yet

            Return:
                {
                    "success": true,
                    "data": {
                        "simulation_id": "sim_xxxx",
                        "file_exists": true,
                        "file_modified_at": "2025-12-04T18:20:00",
                        "is_generating": true, // Whether it is generating
                        "generation_stage": "generating_config", // current generation stage
                        "config": {...} // Configuration content (if exists)
                    }
                }
    """
    import json
    from datetime import datetime
    
    try:
        # Get simulation directory
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        # Configuration file path
        config_file = os.path.join(sim_dir, "simulation_config.json")
        
        # Check if the file exists
        file_exists = os.path.exists(config_file)
        config = None
        file_modified_at = None
        
        if file_exists:
            # Get file modification time
            file_stat = os.stat(config_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to read config file (may be writing): {e}")
                config = None
        
        # Check whether it is being generated (judged by state.json)
        is_generating = False
        generation_stage = None
        config_generated = False
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    config_generated = state_data.get("config_generated", False)
                    
                    # Determine the current stage
                    if is_generating:
                        if state_data.get("profiles_generated", False):
                            generation_stage = "generating_config"
                        else:
                            generation_stage = "generating_profiles"
                    elif status == "ready":
                        generation_stage = "completed"
            except Exception:
                pass
        
        # Build return data
        response_data = {
            "simulation_id": simulation_id,
            "file_exists": file_exists,
            "file_modified_at": file_modified_at,
            "is_generating": is_generating,
            "generation_stage": generation_stage,
            "config_generated": config_generated,
            "config": config
        }
        
        # If the configuration exists, extract some key statistics
        if config:
            response_data["summary"] = {
                "total_agents": len(config.get("agent_configs", [])),
                "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"),
                "initial_posts_count": len(config.get("event_config", {}).get("initial_posts", [])),
                "hot_topics_count": len(config.get("event_config", {}).get("hot_topics", [])),
                "has_twitter_config": "twitter_config" in config,
                "has_reddit_config": "reddit_config" in config,
                "generated_at": config.get("generated_at"),
                "llm_model": config.get("llm_model")
            }
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except Exception as e:
        logger.error(f"Failed to get realtime config: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config', methods=['GET'])
def get_simulation_config(simulation_id: str):
    """
        Get simulation configuration (complete configuration generated intelligently by LLM)

            Return contains:
                - time_config: time configuration (simulation duration, rounds, peak/trough periods)
                - agent_configs: activity configuration of each Agent (activity, speaking frequency, stance, etc.)
                - event_config: event configuration (initial post, hot topics)
                - platform_configs: platform configuration
                - generation_reasoning: LLM configuration reasoning description
    """
    try:
        manager = SimulationManager()
        config = manager.get_simulation_config(simulation_id)
        
        if not config:
            return jsonify({
                "success": False,
                "error": t('api.configNotFound')
            }), 404
        
        return jsonify({
            "success": True,
            "data": config
        })
        
    except Exception as e:
        logger.error(f"Failed to get config: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/download', methods=['GET'])
def download_simulation_config(simulation_id: str):
    """Download simulation configuration file"""
    try:
        manager = SimulationManager()
        sim_dir = manager._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return jsonify({
                "success": False,
                "error": t('api.configFileNotFound')
            }), 404
        
        return send_file(
            config_path,
            as_attachment=True,
            download_name="simulation_config.json"
        )
        
    except Exception as e:
        logger.error(f"Failed to download config: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/script/<script_name>/download', methods=['GET'])
def download_simulation_script(script_name: str):
    """
        Download the simulation run script file (general script, located in backend/scripts/)

            script_name optional values:
                - run_twitter_simulation.py
                - run_reddit_simulation.py
                - run_parallel_simulation.py
                - action_logger.py
    """
    try:
        # The script is located in the backend/scripts/ directory
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        # Validation script name
        allowed_scripts = [
            "run_twitter_simulation.py",
            "run_reddit_simulation.py", 
            "run_parallel_simulation.py",
            "action_logger.py"
        ]
        
        if script_name not in allowed_scripts:
            return jsonify({
                "success": False,
                "error": t('api.unknownScript', name=script_name, allowed=allowed_scripts)
            }), 400
        
        script_path = os.path.join(scripts_dir, script_name)
        
        if not os.path.exists(script_path):
            return jsonify({
                "success": False,
                "error": t('api.scriptFileNotFound', name=script_name)
            }), 404
        
        return send_file(
            script_path,
            as_attachment=True,
            download_name=script_name
        )
        
    except Exception as e:
        logger.error(f"Failed to download script: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Profile generation interface (for independent use) ==============

@simulation_bp.route('/generate-profiles', methods=['POST'])
def generate_profiles():
    """
        Generate OASIS Agent Profile directly from the graph (without creating a simulation)

            Request (JSON):
                {
                    "graph_id": "mirofish_xxxx", // required
                    "entity_types": ["Student"], // optional
                    "use_llm": true, // optional
                    "platform": "reddit" // optional
                }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        if not graph_id:
            return jsonify({
                "success": False,
                "error": t('api.requireGraphId')
            }), 400
        
        entity_types = data.get('entity_types')
        use_llm = data.get('use_llm', True)
        platform = data.get('platform', 'reddit')
        
        reader = ZepEntityReader()
        filtered = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=True
        )
        
        if filtered.filtered_count == 0:
            return jsonify({
                "success": False,
                "error": t('api.noMatchingEntities')
            }), 400
        
        generator = OasisProfileGenerator()
        profiles = generator.generate_profiles_from_entities(
            entities=filtered.entities,
            use_llm=use_llm
        )
        
        if platform == "reddit":
            profiles_data = [p.to_reddit_format() for p in profiles]
        elif platform == "twitter":
            profiles_data = [p.to_twitter_format() for p in profiles]
        else:
            profiles_data = [p.to_dict() for p in profiles]
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "entity_types": list(filtered.entity_types),
                "count": len(profiles_data),
                "profiles": profiles_data
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to generate profiles: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Simulation operation control interface ==============

@simulation_bp.route('/start', methods=['POST'])
def start_simulation():
    """
        Start running the simulation

            Request (JSON):
                {
                    "simulation_id": "sim_xxxx", // required, simulation ID
                    "platform": "parallel", // optional: twitter / reddit / parallel (default)
                    "max_rounds": 100, // Optional: Maximum number of simulation rounds, used to truncate simulations that are too long
                    "enable_graph_memory_update": false, // Optional: Whether to dynamically update Agent activities to Zep graph memory
                    "force": false // Optional: Force restart (will stop the running simulation and clear the log)
                }

            Regarding the force parameter:
                - When enabled, if the simulation is running or has completed, it will first stop and clean the run log
                - The contents to be cleaned include: run_state.json, actions.jsonl, simulation.log, etc.
                - The configuration file (simulation_config.json) and profile files will not be cleaned
                - Suitable for scenarios where the simulation needs to be re-run

            About enable_graph_memory_update:
                - Once enabled, all Agent activities in the simulation (posts, comments, likes, etc.) will be updated to the Zep map in real time
                - This allows the graph to "remember" the simulation process for subsequent analysis or AI dialogue
                - Requires simulation associated projects to have a valid graph_id
                - Adopt batch update mechanism to reduce the number of API calls

            Return:
                {
                    "success": true,
                    "data": {
                        "simulation_id": "sim_xxxx",
                        "runner_status": "running",
                        "process_pid": 12345,
                        "twitter_running": true,
                        "reddit_running": true,
                        "started_at": "2025-12-01T10:00:00",
                        "graph_memory_update_enabled": true, // Whether graph memory update is enabled
                        "force_restarted": true // Is it a forced restart?
                    }
                }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        platform = data.get('platform', 'parallel')
        # Optional: Maximum number of simulation rounds
        # Optional: Whether to enable map memory update
        # Optional: force a restart

        # Verify max_rounds parameter
        if max_rounds is not None:
            try:
                max_rounds = int(max_rounds)
                if max_rounds <= 0:
                    return jsonify({
                        "success": False,
                        "error": t('api.maxRoundsPositive')
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "error": t('api.maxRoundsInvalid')
                }), 400

        if platform not in ['twitter', 'reddit', 'parallel']:
            return jsonify({
                "success": False,
                "error": t('api.invalidPlatform', platform=platform)
            }), 400

        # Check if the simulation is ready
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404

        force_restarted = False
        
        # Intelligent processing status: allow restart if preparation is complete
        if state.status != SimulationStatus.READY:
            # Check if preparations are complete
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)

            if is_prepared:
                # Preparation is complete, check if there are any running processes
                if state.status == SimulationStatus.RUNNING:
                    # Check if the simulated process is actually running
                    run_state = SimulationRunner.get_run_state(simulation_id)
                    if run_state and run_state.runner_status.value == "running":
                        # The process is actually running
                        if force:
                            # Force mode: Stop a running simulation
                            logger.info(f"Force mode: stopping running simulation {simulation_id}")
                            try:
                                SimulationRunner.stop_simulation(simulation_id)
                            except Exception as e:
                                logger.warning(f"Warning while stopping simulation: {str(e)}")
                        else:
                            return jsonify({
                                "success": False,
                                "error": t('api.simRunningForceHint')
                            }), 400

                # If it is forced mode, clear the running log
                if force:
                    logger.info(f"Force mode: cleaning simulation logs {simulation_id}")
                    cleanup_result = SimulationRunner.cleanup_simulation_logs(simulation_id)
                    if not cleanup_result.get("success"):
                        logger.warning(f"Warning while cleaning logs: {cleanup_result.get('errors')}")
                    force_restarted = True

                # The process does not exist or has ended. The reset status is ready.
                logger.info(f"Simulation {simulation_id} preparation complete, resetting status to ready (previous status: {state.status.value})")
                state.status = SimulationStatus.READY
                manager._save_simulation_state(state)
            else:
                # Preparation work not completed
                return jsonify({
                    "success": False,
                    "error": t('api.simNotReady', status=state.status.value)
                }), 400
        
        # Get the map ID (for map memory update)
        graph_id = None
        if enable_graph_memory_update:
            # Get graph_id from simulation state or project
            graph_id = state.graph_id
            if not graph_id:
                # Try to get from project
                project = ProjectManager.get_project(state.project_id)
                if project:
                    graph_id = project.graph_id
            
            if not graph_id:
                return jsonify({
                    "success": False,
                    "error": t('api.graphIdRequiredForMemory')
                }), 400
            
            logger.info(f"Enabling graph memory update: simulation_id={simulation_id}, graph_id={graph_id}")
        
        # Start simulation
        run_state = SimulationRunner.start_simulation(
            simulation_id=simulation_id,
            platform=platform,
            max_rounds=max_rounds,
            enable_graph_memory_update=enable_graph_memory_update,
            graph_id=graph_id
        )
        
        # Update simulation status
        state.status = SimulationStatus.RUNNING
        manager._save_simulation_state(state)
        
        response_data = run_state.to_dict()
        if max_rounds:
            response_data['max_rounds_applied'] = max_rounds
        response_data['graph_memory_update_enabled'] = enable_graph_memory_update
        response_data['force_restarted'] = force_restarted
        if enable_graph_memory_update:
            response_data['graph_id'] = graph_id
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Failed to start simulation: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/stop', methods=['POST'])
def stop_simulation():
    """
        Stop simulation

            Request (JSON):
                {
                    "simulation_id": "sim_xxxx" // Required, simulation ID
                }

            Return:
                {
                    "success": true,
                    "data": {
                        "simulation_id": "sim_xxxx",
                        "runner_status": "stopped",
                        "completed_at": "2025-12-01T12:00:00"
                    }
                }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        run_state = SimulationRunner.stop_simulation(simulation_id)
        
        # Update simulation status
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.PAUSED
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Failed to stop simulation: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Real-time status monitoring interface ==============

@simulation_bp.route('/<simulation_id>/run-status', methods=['GET'])
def get_run_status(simulation_id: str):
    """
        Get the real-time status of the simulation run (for front-end polling)

            Return:
                {
                    "success": true,
                    "data": {
                        "simulation_id": "sim_xxxx",
                        "runner_status": "running",
                        "current_round": 5,
                        "total_rounds": 144,
                        "progress_percent": 3.5,
                        "simulated_hours": 2,
                        "total_simulation_hours": 72,
                        "twitter_running": true,
                        "reddit_running": true,
                        "twitter_actions_count": 150,
                        "reddit_actions_count": 200,
                        "total_actions_count": 350,
                        "started_at": "2025-12-01T10:00:00",
                        "updated_at": "2025-12-01T10:30:00"
                    }
                }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "current_round": 0,
                    "total_rounds": 0,
                    "progress_percent": 0,
                    "twitter_actions_count": 0,
                    "reddit_actions_count": 0,
                    "total_actions_count": 0,
                }
            })
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Failed to get run status: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/run-status/detail', methods=['GET'])
def get_run_status_detail(simulation_id: str):
    """
        Get the detailed status of the simulation run (including all actions)

            Used for front-end display of real-time dynamics

            Query parameters:
                platform: filtering platform (twitter/reddit, optional)

            Return:
                {
                    "success": true,
                    "data": {
                        "simulation_id": "sim_xxxx",
                        "runner_status": "running",
                        "current_round": 5,
                        ...
                        "all_actions": [
                            {
                                "round_num": 5,
                                "timestamp": "2025-12-01T10:30:00",
                                "platform": "twitter",
                                "agent_id": 3,
                                "agent_name": "Agent Name",
                                "action_type": "CREATE_POST",
                                "action_args": {"content": "..."},
                                "result": null,
                                "success": true
                            },
                            ...
                        ],
                        "twitter_actions": [...], # All actions on the Twitter platform
                        "reddit_actions": [...] # All actions on the Reddit platform
                    }
                }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        platform_filter = request.args.get('platform')
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "all_actions": [],
                    "twitter_actions": [],
                    "reddit_actions": []
                }
            })
        
        # Get the full list of actions
        all_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter
        )
        
        # Get actions by platform
        twitter_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="twitter"
        ) if not platform_filter or platform_filter == "twitter" else []
        
        reddit_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="reddit"
        ) if not platform_filter or platform_filter == "reddit" else []
        
        # Get the actions of the current round (recent_actions only displays the latest round)
        current_round = run_state.current_round
        recent_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter,
            round_num=current_round
        ) if current_round > 0 else []
        
        # Get basic status information
        result = run_state.to_dict()
        result["all_actions"] = [a.to_dict() for a in all_actions]
        result["twitter_actions"] = [a.to_dict() for a in twitter_actions]
        result["reddit_actions"] = [a.to_dict() for a in reddit_actions]
        result["rounds_count"] = len(run_state.rounds)
        # recent_actions only displays the latest round of content from the two platforms
        result["recent_actions"] = [a.to_dict() for a in recent_actions]
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"Failed to get detailed status: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/actions', methods=['GET'])
def get_simulation_actions(simulation_id: str):
    """
        Get the history of Agent actions in the simulation

            Query parameters:
                limit: return quantity (default 100)
                offset: offset (default 0)
                platform: filtering platform (twitter/reddit)
                agent_id: Filter Agent ID
                round_num: filter rounds

            Return:
                {
                    "success": true,
                    "data": {
                        "count": 100,
                        "actions": [...]
                    }
                }
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        platform = request.args.get('platform')
        agent_id = request.args.get('agent_id', type=int)
        round_num = request.args.get('round_num', type=int)
        
        actions = SimulationRunner.get_actions(
            simulation_id=simulation_id,
            limit=limit,
            offset=offset,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(actions),
                "actions": [a.to_dict() for a in actions]
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to get action history: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/timeline', methods=['GET'])
def get_simulation_timeline(simulation_id: str):
    """
        Get simulation timeline (summarized by rounds)

            Used for front-end display of progress bar and timeline view

            Query parameters:
                start_round: starting round (default 0)
                end_round: end round (default all)

            Return summary information for each round
    """
    try:
        start_round = request.args.get('start_round', 0, type=int)
        end_round = request.args.get('end_round', type=int)
        
        timeline = SimulationRunner.get_timeline(
            simulation_id=simulation_id,
            start_round=start_round,
            end_round=end_round
        )
        
        return jsonify({
            "success": True,
            "data": {
                "rounds_count": len(timeline),
                "timeline": timeline
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to get timeline: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/agent-stats', methods=['GET'])
def get_agent_stats(simulation_id: str):
    """
        Get statistics for each Agent

            Used for front-end display of Agent activity rankings, action distribution, etc.
    """
    try:
        stats = SimulationRunner.get_agent_stats(simulation_id)
        
        return jsonify({
            "success": True,
            "data": {
                "agents_count": len(stats),
                "stats": stats
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to get agent stats: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Database query interface ==============

@simulation_bp.route('/<simulation_id>/posts', methods=['GET'])
def get_simulation_posts(simulation_id: str):
    """
        Get posts in simulation

            Query parameters:
                platform: platform type (twitter/reddit)
                limit: return quantity (default 50)
                offset: offset

            Return list of posts (read from SQLite database)
    """
    try:
        platform = request.args.get('platform', 'reddit')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_file = f"{platform}_simulation.db"
        db_path = os.path.join(sim_dir, db_file)
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "platform": platform,
                    "count": 0,
                    "posts": [],
                    "message": t('api.dbNotExist')
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT * FROM post 
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
            """, (limit, offset))
            
            posts = [dict(row) for row in cursor.fetchall()]
            
            cursor.execute("SELECT COUNT(*) FROM post")
            total = cursor.fetchone()[0]
            
        except sqlite3.OperationalError:
            posts = []
            total = 0
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "total": total,
                "count": len(posts),
                "posts": posts
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to get posts: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/comments', methods=['GET'])
def get_simulation_comments(simulation_id: str):
    """
        Get comments in a simulation (Reddit only)

            Query parameters:
                post_id: filter post ID (optional)
                limit: return quantity
                offset: offset
    """
    try:
        post_id = request.args.get('post_id')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_path = os.path.join(sim_dir, "reddit_simulation.db")
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "count": 0,
                    "comments": []
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            if post_id:
                cursor.execute("""
                    SELECT * FROM comment 
                    WHERE post_id = ?
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (post_id, limit, offset))
            else:
                cursor.execute("""
                    SELECT * FROM comment 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (limit, offset))
            
            comments = [dict(row) for row in cursor.fetchall()]
            
        except sqlite3.OperationalError:
            comments = []
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(comments),
                "comments": comments
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to get comments: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interview interview interface ==============

@simulation_bp.route('/interview', methods=['POST'])
def interview_agent():
    """
        Interview a single agent

            Note: This function requires the simulation environment to be running (enter the waiting command mode after completing the simulation cycle)

            Request (JSON):
                {
                    "simulation_id": "sim_xxxx", // required, simulation ID
                    "agent_id": 0, // Required, Agent ID
                    "prompt": "What do you think about this matter?", // Required, interview question
                    "platform": "twitter", // Optional, specify the platform (twitter/reddit)
                                                       // When not specified: Dual-platform simulation interviews two platforms at the same time
                    "timeout": 60 // Optional, timeout time (seconds), default 60
                }

            Return (no platform specified, dual platform mode):
                {
                    "success": true,
                    "data": {
                        "agent_id": 0,
                        "prompt": "What do you think about this matter?",
                        "result": {
                            "agent_id": 0,
                            "prompt": "...",
                            "platforms": {
                                "twitter": {"agent_id": 0, "response": "...", "platform": "twitter"},
                                "reddit": {"agent_id": 0, "response": "...", "platform": "reddit"}
                            }
                        },
                        "timestamp": "2025-12-08T10:00:01"
                    }
                }

            Return (specify platform):
                {
                    "success": true,
                    "data": {
                        "agent_id": 0,
                        "prompt": "What do you think about this matter?",
                        "result": {
                            "agent_id": 0,
                            "response": "I think...",
                            "platform": "twitter",
                            "timestamp": "2025-12-08T10:00:00"
                        },
                        "timestamp": "2025-12-08T10:00:01"
                    }
                }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        # Optional: twitter/reddit/None
        timeout = data.get('timeout', 60)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        if agent_id is None:
            return jsonify({
                "success": False,
                "error": t('api.requireAgentId')
            }), 400
        
        if not prompt:
            return jsonify({
                "success": False,
                "error": t('api.requirePrompt')
            }), 400
        
        # Verify platform parameters
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": t('api.invalidInterviewPlatform')
            }), 400
        
        # Check environment status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": t('api.envNotRunning')
            }), 400
        
        # Optimize prompts and add prefixes to avoid Agent calling tools
        optimized_prompt = optimize_interview_prompt(prompt)
        
        result = SimulationRunner.interview_agent(
            simulation_id=simulation_id,
            agent_id=agent_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": t('api.interviewTimeout', error=str(e))
        }), 504
        
    except Exception as e:
        logger.error(f"Interview failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/batch', methods=['POST'])
def interview_agents_batch():
    """
        Interview multiple agents in batches

            Note: This feature requires the simulation environment to be running

            Request (JSON):
                {
                    "simulation_id": "sim_xxxx", // required, simulation ID
                    "interviews": [ // Required, interview list
                        {
                            "agent_id": 0,
                            "prompt": "What do you think of A?",
                            "platform": "twitter" // Optional, specify the interview platform of the Agent
                        },
                        {
                            "agent_id": 1,
                            "prompt": "What do you think of B?" // If platform is not specified, the default value is used
                        }
                    ],
                    "platform": "reddit", // optional, default platform (overridden by each item's platform)
                                                       // When not specified: Dual-platform simulation allows each Agent to interview two platforms at the same time
                    "timeout": 120 // Optional, timeout time (seconds), default 120
                }

            Return:
                {
                    "success": true,
                    "data": {
                        "interviews_count": 2,
                        "result": {
                            "interviews_count": 4,
                            "results": {
                                "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                                "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                                "twitter_1": {"agent_id": 1, "response": "...", "platform": "twitter"},
                                "reddit_1": {"agent_id": 1, "response": "...", "platform": "reddit"}
                            }
                        },
                        "timestamp": "2025-12-08T10:00:01"
                    }
                }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        interviews = data.get('interviews')
        # Optional: twitter/reddit/None
        timeout = data.get('timeout', 120)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        if not interviews or not isinstance(interviews, list):
            return jsonify({
                "success": False,
                "error": t('api.requireInterviews')
            }), 400

        # Verify platform parameters
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": t('api.invalidInterviewPlatform')
            }), 400

        # Verify each interview item
        for i, interview in enumerate(interviews):
            if 'agent_id' not in interview:
                return jsonify({
                    "success": False,
                    "error": t('api.interviewListMissingAgentId', index=i+1)
                }), 400
            if 'prompt' not in interview:
                return jsonify({
                    "success": False,
                    "error": t('api.interviewListMissingPrompt', index=i+1)
                }), 400
            # Verify the platform of each item (if any)
            item_platform = interview.get('platform')
            if item_platform and item_platform not in ("twitter", "reddit"):
                return jsonify({
                    "success": False,
                    "error": t('api.interviewListInvalidPlatform', index=i+1)
                }), 400

        # Check environment status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": t('api.envNotRunning')
            }), 400

        # Optimize the prompt of each interview item and add a prefix to avoid Agent calling tools
        optimized_interviews = []
        for interview in interviews:
            optimized_interview = interview.copy()
            optimized_interview['prompt'] = optimize_interview_prompt(interview.get('prompt', ''))
            optimized_interviews.append(optimized_interview)

        result = SimulationRunner.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=optimized_interviews,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": t('api.batchInterviewTimeout', error=str(e))
        }), 504

    except Exception as e:
        logger.error(f"Batch interview failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/all', methods=['POST'])
def interview_all_agents():
    """
        Global Interview - Interview all Agents using the same questions

            Note: This feature requires the simulation environment to be running

            Request (JSON):
                {
                    "simulation_id": "sim_xxxx", // required, simulation ID
                    "prompt": "What is your overall opinion on this matter?", // Required, interview question (all Agents use the same question)
                    "platform": "reddit", // Optional, specify the platform (twitter/reddit)
                                                            // When not specified: Dual-platform simulation allows each Agent to interview two platforms at the same time
                    "timeout": 180 // Optional, timeout time (seconds), default 180
                }

            Return:
                {
                    "success": true,
                    "data": {
                        "interviews_count": 50,
                        "result": {
                            "interviews_count": 100,
                            "results": {
                                "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                                "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                                ...
                            }
                        },
                        "timestamp": "2025-12-08T10:00:01"
                    }
                }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        prompt = data.get('prompt')
        # Optional: twitter/reddit/None
        timeout = data.get('timeout', 180)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        if not prompt:
            return jsonify({
                "success": False,
                "error": t('api.requirePrompt')
            }), 400

        # Verify platform parameters
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": t('api.invalidInterviewPlatform')
            }), 400

        # Check environment status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": t('api.envNotRunning')
            }), 400

        # Optimize prompts and add prefixes to avoid Agent calling tools
        optimized_prompt = optimize_interview_prompt(prompt)

        result = SimulationRunner.interview_all_agents(
            simulation_id=simulation_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": t('api.globalInterviewTimeout', error=str(e))
        }), 504

    except Exception as e:
        logger.error(f"Global interview failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/history', methods=['POST'])
def get_interview_history():
    """
        Get Interview history

            Read all Interview records from the mock database

            Request (JSON):
                {
                    "simulation_id": "sim_xxxx", // required, simulation ID
                    "platform": "reddit", // Optional, platform type (reddit/twitter)
                                                   // If not specified, all histories of the two platforms will be returned.
                    "agent_id": 0, // Optional, only get the interview history of this Agent
                    "limit": 100 // Optional, return quantity, default 100
                }

            Return:
                {
                    "success": true,
                    "data": {
                        "count": 10,
                        "history": [
                            {
                                "agent_id": 0,
                                "response": "I think...",
                                "prompt": "What do you think about this matter?",
                                "timestamp": "2025-12-08T10:00:00",
                                "platform": "reddit"
                            },
                            ...
                        ]
                    }
                }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        # If not specified, the history of the two platforms will be returned.
        agent_id = data.get('agent_id')
        limit = data.get('limit', 100)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        history = SimulationRunner.get_interview_history(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": {
                "count": len(history),
                "history": history
            }
        })

    except Exception as e:
        logger.error(f"Failed to get interview history: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/env-status', methods=['POST'])
def get_env_status():
    """
        Get simulation environment status

            Check whether the simulation environment is alive (can receive Interview command)

            Request (JSON):
                {
                    "simulation_id": "sim_xxxx" // Required, simulation ID
                }

            Return:
                {
                    "success": true,
                    "data": {
                        "simulation_id": "sim_xxxx",
                        "env_alive": true,
                        "twitter_available": true,
                        "reddit_available": true,
                        "message": "The environment is running and can receive Interview commands"
                    }
                }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        env_alive = SimulationRunner.check_env_alive(simulation_id)
        
        # Get more detailed status information
        env_status = SimulationRunner.get_env_status_detail(simulation_id)

        if env_alive:
            message = t('api.envRunning')
        else:
            message = t('api.envNotRunningShort')

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "env_alive": env_alive,
                "twitter_available": env_status.get("twitter_available", False),
                "reddit_available": env_status.get("reddit_available", False),
                "message": message
            }
        })

    except Exception as e:
        logger.error(f"Failed to get environment status: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/close-env', methods=['POST'])
def close_simulation_env():
    """
        Close simulation environment

            Send a shutdown environment command to the simulation to gracefully exit wait-for-command mode.

            Note: This is different from the /stop interface, which will forcefully terminate the process.
            This interface will allow the simulation to gracefully shut down the environment and exit.

            Request (JSON):
                {
                    "simulation_id": "sim_xxxx", // required, simulation ID
                    "timeout": 30 // Optional, timeout time (seconds), default 30
                }

            Return:
                {
                    "success": true,
                    "data": {
                        "message": "Environment shutdown command has been sent",
                        "result": {...},
                        "timestamp": "2025-12-08T10:00:01"
                    }
                }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        timeout = data.get('timeout', 30)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        result = SimulationRunner.close_simulation_env(
            simulation_id=simulation_id,
            timeout=timeout
        )
        
        # Update simulation status
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.COMPLETED
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Failed to close environment: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
