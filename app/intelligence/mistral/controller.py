from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from app.intelligence.mistral.client import generate
from app.intelligence.mistral.prompt import build_conflict_prompt
from app.intelligence.mistral.parser import parse


class MistralController:
    """Mistral is consulted only for conflicts already detected deterministically."""

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._lock = Lock()
        self._conflict_state = {}

    @staticmethod
    def conflict_key(conflict):
        return (str(conflict["waypoint"]).upper(),) + tuple(sorted(
            str(aircraft.callsign) for aircraft in conflict["aircraft"]
        ))

    def request_conflict(self, conflict, holding_manager, on_result):
        key = self.conflict_key(conflict)
        with self._lock:
            if key in self._conflict_state:
                return False
            self._conflict_state[key] = {"status": "pending", "callsigns": set(key[1:])}
        self.executor.submit(self._decide_conflict, key, conflict, holding_manager, on_result)
        return True

    def _decide_conflict(self, key, conflict, holding_manager, on_result):
        result = None
        try:
            response = generate(build_conflict_prompt(conflict))
            decision = parse(response)
            if decision is None:
                raise ValueError("Mistral returned invalid conflict-decision JSON")
            if decision["decision"] == "NONE":
                result = {"decision": decision, "conflict": conflict, "holding": False}
                return

            involved = {str(ac.callsign): ac for ac in conflict["aircraft"]}
            selected = involved.get(decision["callsign"])
            if selected is None:
                raise ValueError("Mistral selected an aircraft outside the detected conflict")
            if decision["holding_fix"].strip().upper() != str(conflict["waypoint"]).upper():
                raise ValueError("Mistral selected a holding fix other than the conflict waypoint")
            compatible = holding_manager.compatible_holding_route_ids(selected, conflict["waypoint"])
            if decision["holding_route_id"] not in compatible:
                raise ValueError("Mistral selected a nonexistent or incompatible holding route")

            holding_route = holding_manager._holding_route_by_id(decision["holding_route_id"])
            altitude = decision.get("altitude")
            speed = decision.get("speed")
            if altitude is not None:
                lower = self._flight_level(holding_route.get("lower_limit"))
                upper = self._flight_level(holding_route.get("upper_limit"))
                if (lower is not None and altitude < lower) or (upper is not None and altitude > upper):
                    altitude = None
            maximum_speed = holding_route.get("max_speed_kt")
            if speed is not None and maximum_speed is not None and speed > maximum_speed:
                speed = None

            applied = holding_manager.apply_holding_route(selected, decision["holding_route_id"])
            if not applied.get("success"):
                raise ValueError(applied.get("message", "Holding route could not be applied"))
            decision["altitude"] = altitude
            decision["speed"] = speed
            if altitude is not None:
                selected.assign_altitude(altitude)
            if speed is not None:
                selected.assign_speed(speed)
            result = {"decision": decision, "conflict": conflict, "holding": True,
                      "selected_aircraft": selected}
        except Exception as exc:
            print(f"MISTRAL CONFLICT DECISION FAILED at {conflict.get('waypoint')}: {exc}")
        finally:
            with self._lock:
                state = self._conflict_state.get(key)
                if state is not None:
                    state["status"] = "holding" if result and result.get("holding") else "decided"
                    if result and result.get("holding"):
                        state["held_callsign"] = result["selected_aircraft"].callsign
            if result is not None:
                on_result(result)

    def clear_conflicts(self, detected_keys, active_holding_callsigns=()):
        active_holding_callsigns = set(active_holding_callsigns)
        with self._lock:
            for key, state in list(self._conflict_state.items()):
                if key in detected_keys:
                    continue
                if state.get("status") == "pending":
                    continue
                if state.get("held_callsign") in active_holding_callsigns:
                    continue
                del self._conflict_state[key]

    def release_for_callsign(self, callsign):
        with self._lock:
            for key, state in list(self._conflict_state.items()):
                if callsign in key[1:] or state.get("held_callsign") == callsign:
                    del self._conflict_state[key]

    @staticmethod
    def _flight_level(value):
        if not isinstance(value, str):
            return None
        text = value.strip().upper()
        if text.startswith("FL") and text[2:].isdigit():
            return int(text[2:]) * 100
        return None


mistral_controller = MistralController()
