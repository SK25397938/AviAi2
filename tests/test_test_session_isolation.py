import pytest

from app.simulation import world as world_module
from app.simulation.engine import SimulationEngine


class FakeManager:
    def __init__(self):
        self.aircraft = []

    def all(self):
        return self.aircraft


class FakeHoldingManager:
    def __init__(self):
        self._active_holds = {"test": {}}


class FakeEngine:
    instances = []

    def __init__(self, **kwargs):
        self.session_mode = kwargs.get("session_mode", "NORMAL")
        self.session_id = kwargs.get("session_id")
        self.manager = FakeManager()
        self.holding_manager = FakeHoldingManager()
        self.approach_reservations = {"APP25": object()}
        self.approach_reservation_status = {"APP25": "PENDING"}
        self._last_separation_event_at = {"TST0001": 1}
        self._last_holding_release_diagnostic_at = {"TST0001": 1}
        self.aircraft_db = self.traffic_manager = self.scheduler = self
        self.closed = 0
        self.__class__.instances.append(self)

    def close(self):
        self.closed += 1


def make_world(engine):
    world = world_module.SimulationWorld.__new__(world_module.SimulationWorld)
    world.airport = "VABB"
    world.engine = engine
    world.session_mode = "NORMAL"
    world.session_id = None
    world._normal_engine = None
    return world


def test_engine_test_mode_blocks_automatic_traffic_entry_points():
    engine = SimulationEngine.__new__(SimulationEngine)
    engine.session_mode = "TEST"
    assert engine.spawn_due_arrivals(1) == []
    assert engine.process_due_departures() == []
    with pytest.raises(RuntimeError, match="disabled in TEST"):
        engine.spawn("A320", "AIC101", "STAR1")


def test_session_swaps_isolated_engine_and_restores_normal_engine(monkeypatch):
    FakeEngine.instances.clear()
    normal = FakeEngine()
    world = make_world(normal)
    monkeypatch.setattr(world_module, "SimulationEngine", FakeEngine)

    first = world.enter_test_session()
    test_engine = world.engine
    second = world.enter_test_session()

    assert first["session_mode"] == second["session_mode"] == "TEST"
    assert first["session_id"] == second["session_id"]
    assert world.engine is test_engine and test_engine is not normal
    assert len(FakeEngine.instances) == 2

    test_engine.manager.aircraft.append(type("Aircraft", (), {"test_session_id": first["session_id"]})())
    restored = world.exit_test_session(first["session_id"])

    assert restored["session_mode"] == "NORMAL"
    assert world.engine is normal
    assert test_engine.manager.aircraft == []
    assert not test_engine.approach_reservations
    assert not test_engine.approach_reservation_status
    assert not test_engine.holding_manager._active_holds
    assert test_engine.closed == 3


def test_stale_test_session_tokens_cannot_spawn_after_exit(monkeypatch):
    world = make_world(FakeEngine())
    monkeypatch.setattr(world_module, "SimulationEngine", FakeEngine)
    session = world.enter_test_session()
    world.exit_test_session(session["session_id"])
    with pytest.raises(ValueError, match="not active"):
        world.spawn_random_test_aircraft(session["session_id"], "APP25", "route")
