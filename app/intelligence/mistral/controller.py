from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from app.intelligence.mistral.client import generate
from app.intelligence.mistral.prompt import build_conflict_prompt
from app.intelligence.mistral.parser import parse


class MistralController:
    """Mistral advises on detected conflicts; deterministic safety is the fallback."""

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._lock = Lock()
        self._conflict_state = {}

    @staticmethod
    def conflict_key(conflict):
        return (str(conflict["waypoint"]).upper(),) + tuple(sorted(
            str(aircraft.callsign) for aircraft in conflict["aircraft"]
        ))

    @staticmethod
    def _priority_and_trailing_callsigns(conflict):
        timing = conflict.get("aircraft_timing", {})
        ordered = sorted(
            conflict["aircraft"],
            key=lambda aircraft: (
                float(timing.get(str(aircraft.callsign), {}).get("eta_to_fix_seconds", float("inf"))),
                str(aircraft.callsign),
            ),
        )
        return str(ordered[0].callsign), str(ordered[-1].callsign)

    def request_conflict(self, conflict, holding_manager, on_result):
        key = self.conflict_key(conflict)
        priority_callsign, trailing_callsign = self._priority_and_trailing_callsigns(conflict)
        conflict["priority_callsign"] = priority_callsign
        conflict["trailing_callsign"] = trailing_callsign
        with self._lock:
            if key in self._conflict_state:
                return False
            self._conflict_state[key] = {
                "status": "pending", "callsigns": {trailing_callsign},
                "priority_callsign": priority_callsign,
                "trailing_callsign": trailing_callsign,
            }
        self.executor.submit(self._decide_conflict, key, conflict, holding_manager, on_result)
        return True

    def _decide_conflict(self, key, conflict, holding_manager, on_result):
        result = None
        decision_source = "Mistral"
        try:
            response = generate(build_conflict_prompt(conflict))
            decision = parse(response)
            if decision is None:
                raise ValueError("Mistral returned invalid conflict-decision JSON")
            if decision["decision"] == "NONE":
                result = self._apply_deterministic_hold(conflict, holding_manager)
                decision_source = "deterministic_fallback"
            else:
                result = self._apply_model_hold(decision, conflict, holding_manager)
        except Exception as exc:
            print(f"MISTRAL DECISION INVALID | reason={exc} | fallback=deterministic")
            result = self._apply_deterministic_hold(conflict, holding_manager)
            decision_source = "deterministic_fallback"

        result["decision_source"] = decision_source
        decision = result["decision"]
        if result["holding"]:
            print(f"AI DECISION | source={decision_source} decision=HOLD "
                  f"callsign={decision['callsign']} holding_route_id={decision['holding_route_id']} "
                  f"holding_fix={decision['holding_fix']}")
        else:
            print(f"AI DECISION | source={decision_source} decision=NONE "
                  f"fix={conflict.get('waypoint')} reason={decision['reason']}")

        with self._lock:
            state = self._conflict_state.get(key)
            if state is not None:
                state["status"] = "holding" if result["holding"] else "decided"
                if result["holding"]:
                    state["held_callsign"] = result["selected_aircraft"].callsign
                    hold = holding_manager.state_for(result["selected_aircraft"])
                    if hold is not None:
                        hold["priority_callsign"] = conflict["priority_callsign"]
                        hold["protected_merge_point"] = conflict["waypoint"]
        if on_result is not None:
            on_result(result)

    @staticmethod
    def _apply_model_hold(decision, conflict, holding_manager):
        involved = {str(ac.callsign): ac for ac in conflict["aircraft"]}
        selected = involved.get(decision["callsign"])
        if selected is None:
            raise ValueError("Mistral selected an aircraft outside the detected conflict")
        if str(selected.callsign) != str(conflict.get("trailing_callsign")):
            raise ValueError("Only the trailing aircraft may be delayed for this merge conflict")
        options = holding_manager.compatible_holding_options(selected, conflict["waypoint"])
        option = next((item for item in options
                       if str(item["holding_route_id"]) == decision["holding_route_id"]
                       and str(item["holding_fix"]).upper() == decision["holding_fix"].upper()), None)
        if option is None:
            raise ValueError("Mistral selected a nonexistent or incompatible holding route")
        expected_rejoin = holding_manager.expected_rejoin_node(selected, decision["holding_route_id"])
        if (decision.get("rejoin_node") != expected_rejoin
                or (expected_rejoin is not None and expected_rejoin not in selected.route)):
            raise ValueError("Mistral selected a rejoin node other than the permanent route's next node")

        holding_route = holding_manager._holding_route_by_id(decision["holding_route_id"])
        altitude, speed = decision.get("altitude"), decision.get("speed")
        if altitude is not None:
            lower = MistralController._flight_level(holding_route.get("lower_limit"))
            upper = MistralController._flight_level(holding_route.get("upper_limit"))
            if ((lower is not None and altitude < lower) or (upper is not None and altitude > upper)):
                raise ValueError("Mistral selected an altitude outside the configured holding limits")
        maximum_speed = holding_route.get("max_speed_kt")
        if speed is not None and maximum_speed is not None and speed > maximum_speed:
            raise ValueError("Mistral selected a speed above the configured holding limit")
        applied = holding_manager.apply_holding_route(
            selected, decision["holding_route_id"], decision["hold_circuits"]
        )
        if not applied.get("success"):
            raise ValueError(applied.get("message", "Holding route could not be applied"))
        # HoldingManager assigns a conflict-free vertical level and the
        # configured speed cap; retain those deterministic values.
        decision["altitude"] = getattr(selected, "holding_altitude_ft", altitude)
        decision["speed"] = getattr(selected, "holding_speed_kts", speed)
        decision["instruction"] = (
            f"Hold at {option['holding_fix']}, maintain {decision['altitude']} feet "
            f"and {float(decision['speed']):g} knots"
        )
        selected.last_instruction = decision["instruction"]
        return {"decision": decision, "conflict": conflict, "holding": True,
                "selected_aircraft": selected}

    @staticmethod
    def _apply_deterministic_hold(conflict, holding_manager):
        fix = str(conflict.get("waypoint", ""))
        timing = conflict.get("aircraft_timing", {})
        candidates = sorted(
            ((aircraft, option) for aircraft in conflict.get("aircraft", ())
             if str(aircraft.callsign) == str(conflict.get("trailing_callsign"))
             for option in holding_manager.compatible_holding_options(aircraft, fix)),
            key=lambda candidate: (
                -float(timing.get(str(candidate[0].callsign), {}).get("eta_to_fix_seconds", 0)),
                str(candidate[0].callsign),
            ),
        )

        for selected, option in candidates:
            holding_route_id = option["holding_route_id"]
            holding_fix = option["holding_fix"]
            rejoin_node = option["rejoin_node"]
            applied = holding_manager.apply_holding_route(selected, holding_route_id, circuits=1)
            if not applied.get("success"):
                print(f"HOLD ACTIVATION FAILED | callsign={selected.callsign} "
                      f"holding_route={holding_route_id} hold_fix={holding_fix} "
                      f"reason={applied.get('message')}")
                continue

            altitude = getattr(selected, "holding_altitude_ft", None)
            speed = getattr(selected, "holding_speed_kts", None)
            instruction = f"Hold at {holding_fix}, maintain {altitude} feet and {float(speed):g} knots"
            selected.last_instruction = instruction
            decision = {
                "controller": "Arrival", "decision": "HOLD", "callsign": selected.callsign,
                "instruction": instruction, "holding_route_id": holding_route_id,
                "holding_fix": holding_fix, "altitude": altitude, "speed": speed,
                "hold_circuits": 1, "rejoin_node": rejoin_node,
                "reason": "Predicted separation at the shared fix is below the required threshold; deterministic fallback applied.",
            }
            return {
                "decision": decision, "conflict": conflict, "holding": True,
                "selected_aircraft": selected, "decision_source": "deterministic_fallback",
            }

        decision = {
            "controller": "Arrival", "decision": "NONE", "callsign": None,
            "instruction": "No compatible holding route available",
            "holding_route_id": None, "holding_fix": None, "altitude": None,
            "speed": None, "hold_circuits": None, "rejoin_node": None,
            "reason": f"Unsafe predicted separation at {fix}, but no involved aircraft has a compatible hold ahead on its route.",
        }
        return {
            "decision": decision, "conflict": conflict, "holding": False,
            "decision_source": "deterministic_fallback",
        }

    @staticmethod
    def _flight_level(value):
        if not isinstance(value, str):
            return None
        text = value.strip().upper()
        if text.startswith("FL") and text[2:].isdigit():
            return int(text[2:]) * 100
        return None

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

    def has_pending_conflict_for(self, callsign):
        with self._lock:
            return any(
                state.get("status") == "pending" and callsign in state.get("callsigns", set())
                for state in self._conflict_state.values()
            )

mistral_controller = MistralController()
