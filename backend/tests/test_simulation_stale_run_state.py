import json
import os

from app.services.simulation_runner import (
    RunnerStatus,
    STALE_PROCESS_ERROR,
    SimulationRunState,
    SimulationRunner,
)


def test_reconcile_stale_run_state_marks_failed_when_pid_missing(tmp_path, monkeypatch):
    simulation_id = "sim_stale"
    sim_dir = tmp_path / simulation_id
    sim_dir.mkdir(parents=True)

    run_state = SimulationRunState(
        simulation_id=simulation_id,
        runner_status=RunnerStatus.RUNNING,
        process_pid=999999,
        started_at="2026-07-01T12:00:00",
    )

    state_file = sim_dir / "run_state.json"
    with open(state_file, "w", encoding="utf-8") as handle:
        json.dump(run_state.to_detail_dict(), handle, ensure_ascii=False, indent=2)

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    SimulationRunner._run_states.clear()
    SimulationRunner._processes.clear()

    loaded = SimulationRunner.get_run_state(simulation_id)

    assert loaded is not None
    assert loaded.runner_status == RunnerStatus.FAILED
    assert loaded.error == STALE_PROCESS_ERROR
    assert loaded.twitter_running is False
    assert loaded.reddit_running is False
    assert SimulationRunner.is_simulation_process_alive(simulation_id, loaded) is False

    with open(state_file, "r", encoding="utf-8") as handle:
        persisted = json.load(handle)

    assert persisted["runner_status"] == "failed"
    assert persisted["error"] == STALE_PROCESS_ERROR


def test_active_in_memory_process_is_not_reconciled_as_stale(monkeypatch):
    from types import SimpleNamespace

    simulation_id = "sim_live"
    state = SimulationRunState(
        simulation_id=simulation_id,
        runner_status=RunnerStatus.RUNNING,
        process_pid=12345,
    )

    SimulationRunner._run_states[simulation_id] = state
    SimulationRunner._processes[simulation_id] = SimpleNamespace(poll=lambda: None)

    reconciled = SimulationRunner._reconcile_stale_run_state(state)

    assert reconciled.runner_status == RunnerStatus.RUNNING
    assert reconciled.error is None

    SimulationRunner._run_states.clear()
    SimulationRunner._processes.clear()
