import json
from pathlib import Path

from app.services import simulation_manager as simulation_manager_module
from app.services.simulation_manager import SimulationManager, SimulationStatus


class _Entity:
    def __init__(self, name: str, entity_type: str = "person"):
        self.name = name
        self.uuid = f"uuid_{name}"
        self.summary = f"{name} summary"
        self.attributes = {}
        self._entity_type = entity_type

    def get_entity_type(self):
        return self._entity_type


class _FakeSimulationParams:
    generation_reasoning = "config reasoning"

    def to_json(self):
        return json.dumps(
            {
                "agent_configs": [{"agent_id": 1}, {"agent_id": 2}],
                "time_config": {"total_simulation_hours": 12, "minutes_per_round": 30},
                "event_config": {"initial_posts": [], "hot_topics": []},
            }
        )


def test_prepare_simulation_reports_internal_config_progress(monkeypatch, tmp_path):
    simulation_manager_module.SimulationManager.SIMULATION_DATA_DIR = str(tmp_path)

    entities = [_Entity("Alice"), _Entity("Bob")]
    profile_save_calls = []

    class _FakeReader:
        def filter_defined_entities(self, **kwargs):
            return type(
                "Filtered",
                (),
                {
                    "filtered_count": len(entities),
                    "entity_types": {"person"},
                    "entities": entities,
                },
            )()

    class _FakeProfileGenerator:
        def __init__(self, graph_id=None):
            self.graph_id = graph_id

        def generate_profiles_from_entities(self, entities, progress_callback=None, **kwargs):
            if progress_callback:
                progress_callback(1, len(entities), "profile Alice")
                progress_callback(2, len(entities), "profile Bob")
            return [{"name": "Alice"}, {"name": "Bob"}]

        def save_profiles(self, profiles, file_path, platform):
            profile_save_calls.append((platform, file_path))
            path = Path(file_path)
            if platform == "reddit":
                path.write_text("[]", encoding="utf-8")
            else:
                path.write_text("username\n", encoding="utf-8")

    manager = SimulationManager()

    class _FakeConfigGenerator:
        def generate_config(self, simulation_id, progress_callback=None, **kwargs):
            assert progress_callback is not None

            current_state = manager.get_simulation(simulation_id)
            assert current_state.status == SimulationStatus.PREPARING
            assert current_state.profiles_generated is True
            assert current_state.config_generated is False

            progress_callback(1, 4, "time config")
            progress_callback(3, 4, "agent batch")
            return _FakeSimulationParams()

    monkeypatch.setattr(simulation_manager_module, "ZepEntityReader", _FakeReader)
    monkeypatch.setattr(
        simulation_manager_module, "OasisProfileGenerator", _FakeProfileGenerator
    )
    monkeypatch.setattr(
        simulation_manager_module, "SimulationConfigGenerator", _FakeConfigGenerator
    )

    state = manager.create_simulation("proj_test", "graph_test")
    progress_events = []

    result = manager.prepare_simulation(
        simulation_id=state.simulation_id,
        simulation_requirement="test requirement",
        document_text="test document",
        progress_callback=lambda stage, progress, message, **kwargs: progress_events.append(
            {
                "stage": stage,
                "progress": progress,
                "message": message,
                **kwargs,
            }
        ),
    )

    assert result.status == SimulationStatus.READY
    assert result.profiles_generated is True
    assert result.config_generated is True
    assert result.config_reasoning == "config reasoning"

    config_progress_messages = [
        event for event in progress_events if event["message"] in {"time config", "agent batch"}
    ]
    assert [event["stage"] for event in config_progress_messages] == [
        "generating_config",
        "generating_config",
    ]
    assert [event["current"] for event in config_progress_messages] == [1, 3]
    assert [event["total"] for event in config_progress_messages] == [4, 4]
    assert config_progress_messages[0]["progress"] < config_progress_messages[1]["progress"]

    persisted_state = manager.get_simulation(state.simulation_id)
    assert persisted_state.status == SimulationStatus.READY
    assert persisted_state.profiles_generated is True
    assert persisted_state.config_generated is True

    assert {platform for platform, _ in profile_save_calls} == {"reddit", "twitter"}
    assert (tmp_path / state.simulation_id / "simulation_config.json").exists()
