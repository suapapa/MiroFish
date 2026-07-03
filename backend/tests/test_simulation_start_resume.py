from types import SimpleNamespace

from app import create_app
from app.api import simulation as simulation_api
from app.config import Config
from app.services.simulation_manager import SimulationStatus
from app.services.simulation_runner import RunnerStatus, SimulationRunState


class _TestConfig(Config):
    TESTING = True
    DEBUG = True
    SECRET_KEY = "test-secret"


def test_start_attaches_to_existing_running_simulation(monkeypatch):
    app = create_app(_TestConfig)
    client = app.test_client()

    fake_state = SimpleNamespace(
        simulation_id="sim_resume",
        project_id="proj_resume",
        graph_id="graph_resume",
        status=SimulationStatus.RUNNING,
    )

    class _FakeManager:
        def get_simulation(self, simulation_id):
            assert simulation_id == "sim_resume"
            return fake_state

        def _save_simulation_state(self, state):
            fake_state.status = state.status

    run_state = SimulationRunState(
        simulation_id="sim_resume",
        runner_status=RunnerStatus.RUNNING,
        current_round=5,
        total_rounds=20,
        process_pid=4321,
        started_at="2026-07-01T12:00:00",
    )

    monkeypatch.setattr(simulation_api, "SimulationManager", _FakeManager)
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "get_run_state",
        lambda simulation_id: run_state,
    )
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "is_simulation_process_alive",
        lambda simulation_id, state=None: True,
    )
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "_graph_memory_enabled",
        {},
        raising=False,
    )

    def _unexpected_start(*args, **kwargs):
        raise AssertionError("start_simulation should not be called while attaching")

    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "start_simulation",
        _unexpected_start,
    )

    response = client.post(
        "/api/simulation/start",
        json={"simulation_id": "sim_resume", "platform": "parallel"},
    )

    assert response.status_code == 200

    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["resumed"] is True
    assert payload["data"]["force_restarted"] is False
    assert payload["data"]["runner_status"] == "running"
    assert payload["data"]["process_pid"] == 4321


def test_start_does_not_attach_to_stale_running_simulation(monkeypatch):
    app = create_app(_TestConfig)
    client = app.test_client()

    fake_state = SimpleNamespace(
        simulation_id="sim_stale",
        project_id="proj_stale",
        graph_id="graph_stale",
        status=SimulationStatus.FAILED,
    )

    class _FakeManager:
        def get_simulation(self, simulation_id):
            assert simulation_id == "sim_stale"
            return fake_state

        def _save_simulation_state(self, state):
            fake_state.status = state.status

    run_state = SimulationRunState(
        simulation_id="sim_stale",
        runner_status=RunnerStatus.FAILED,
        current_round=2,
        total_rounds=20,
        process_pid=999999,
        started_at="2026-07-01T12:00:00",
        error="Simulation process is no longer running (server may have restarted)",
    )

    started = {"called": False}

    def _fake_start(*args, **kwargs):
        started["called"] = True
        return SimulationRunState(
            simulation_id="sim_stale",
            runner_status=RunnerStatus.RUNNING,
            process_pid=5555,
        )

    monkeypatch.setattr(simulation_api, "SimulationManager", _FakeManager)
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "get_run_state",
        lambda simulation_id: run_state,
    )
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "is_simulation_process_alive",
        lambda simulation_id, state=None: False,
    )
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "_graph_memory_enabled",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "start_simulation",
        _fake_start,
    )
    monkeypatch.setattr(
        simulation_api,
        "_check_simulation_prepared",
        lambda simulation_id: (True, {}),
    )

    response = client.post(
        "/api/simulation/start",
        json={"simulation_id": "sim_stale", "platform": "parallel"},
    )

    assert response.status_code == 200
    assert started["called"] is True

    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"].get("resumed") is not True
