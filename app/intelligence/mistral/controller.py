from threading import RLock
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
import math
import json

from app.intelligence.mistral import client as mistral_client
from app.intelligence.mistral.parser import parse
from app.intelligence.mistral.prompt import build_conflict_prompt


class MistralController:
    """Mistral advises on detected conflicts; deterministic safety is the fallback."""

    def __init__(self):
        self._lock = RLock()
        self._conflict_state = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="arrival-ai")
        self._pending = {}
        self._operational_pending = {}
        self._operational_completed = {}
        self._futures = set()
        self.max_pending_decisions = 2
        self.request_timeout_seconds = 3.0

    def operational_choice(self, decision_id, prompt, allowed_choices, snapshot,
                           deterministic_fallback, *, timeout_seconds=None):
        """Return an AI choice when current, otherwise pending or safe fallback."""
        now = monotonic()
        with self._lock:
            completed = self._operational_completed.get(decision_id)
            if completed is not None:
                if self._operational_snapshot_matches(completed["snapshot"], snapshot):
                    return dict(completed["result"])
                self._operational_completed.pop(decision_id, None)
            pending = self._operational_pending.get(decision_id)
            if pending is None:
                if mistral_client.client is None:
                    result = {"status": "decided", "choice": deterministic_fallback,
                              "source": "deterministic_fallback", "reason": "Mistral client unavailable"}
                    self._remember_operational(decision_id, result, snapshot)
                    return dict(result)
                if len(self._futures) >= self.max_pending_decisions:
                    result = {"status": "decided", "choice": deterministic_fallback,
                              "source": "deterministic_fallback", "reason": "AI decision concurrency limit reached"}
                    self._remember_operational(decision_id, result, snapshot)
                    return dict(result)
                try:
                    future = self._executor.submit(mistral_client.generate, prompt)
                    self._track_future(future)
                except Exception as exc:
                    result = {"status": "decided", "choice": deterministic_fallback,
                              "source": "deterministic_fallback", "reason": f"AI request could not start: {exc}"}
                    self._remember_operational(decision_id, result, snapshot)
                    return dict(result)
                self._operational_pending[decision_id] = {
                    "future": future, "started": now, "snapshot": dict(snapshot),
                    "timeout": timeout_seconds or self.request_timeout_seconds,
                    "allowed": set(allowed_choices),
                }
                return {"status": "pending", "choice": None, "source": None,
                        "reason": "AI recommendation pending"}

            if not self._operational_snapshot_matches(pending["snapshot"], snapshot):
                pending["future"].cancel()
                self._operational_pending.pop(decision_id, None)
                result = {"status": "decided", "choice": deterministic_fallback,
                          "source": "deterministic_fallback", "reason": "AI response rejected as stale"}
                self._remember_operational(decision_id, result, snapshot)
                return dict(result)

            if pending.get("fallback_applied"):
                if pending["future"].done():
                    self._operational_pending.pop(decision_id, None)
                return dict(pending["fallback_result"])

            future = pending["future"]
            if not future.done() and now - pending["started"] < pending["timeout"]:
                return {"status": "pending", "choice": None, "source": None,
                        "reason": "AI recommendation pending"}
            if not future.done():
                future.cancel()
                result = {"status": "decided", "choice": deterministic_fallback,
                          "source": "deterministic_fallback", "reason": "AI decision timed out"}
                pending["fallback_applied"] = True
                pending["fallback_result"] = result
                self._remember_operational(decision_id, result, snapshot)
                return dict(result)
            self._operational_pending.pop(decision_id, None)
            try:
                response = future.result(timeout=0)
                data = json.loads(response) if isinstance(response, str) else None
                choice = data.get("choice") if isinstance(data, dict) else None
                if (not isinstance(choice, str) or choice not in pending["allowed"]
                        or not isinstance(data.get("reason"), str)):
                    raise ValueError("AI response did not contain an allowed choice and reason")
                result = {"status": "decided", "choice": choice, "source": "Mistral",
                          "reason": data["reason"]}
            except Exception as exc:
                result = {"status": "decided", "choice": deterministic_fallback,
                          "source": "deterministic_fallback", "reason": f"Invalid AI response: {exc}"}
            self._remember_operational(decision_id, result, snapshot)
            return dict(result)

    def _remember_operational(self, decision_id, result, snapshot):
        self._operational_completed[decision_id] = {
            "result": dict(result), "snapshot": dict(snapshot),
        }
        while len(self._operational_completed) > 256:
            self._operational_completed.pop(next(iter(self._operational_completed)))

    def _track_future(self, future):
        self._futures.add(future)
        future.add_done_callback(self._discard_future)

    def _discard_future(self, future):
        with self._lock:
            self._futures.discard(future)
            for decision_id, pending in list(self._operational_pending.items()):
                if pending.get("future") is future and pending.get("fallback_applied"):
                    self._operational_pending.pop(decision_id, None)

    def clear_operational_decision(self, decision_id):
        with self._lock:
            self._operational_completed.pop(decision_id, None)

    @staticmethod
    def _operational_snapshot_matches(previous, current):
        if set(previous) != set(current):
            return False
        for key, before in previous.items():
            after = current[key]
            if key in {"latitude", "longitude"}:
                continue
            if before != after:
                return False
        if "latitude" in previous and "longitude" in previous:
            mean_lat = (float(previous["latitude"]) + float(current["latitude"])) / 2
            moved_nm = (((float(current["latitude"]) - float(previous["latitude"])) * 60) ** 2
                        + ((float(current["longitude"]) - float(previous["longitude"])) * 60
                           * math.cos(math.radians(mean_lat))) ** 2) ** 0.5
            return moved_nm <= 2.0
        return True

    @staticmethod
    def conflict_key(conflict):
        scope = tuple(conflict.get("session_scope", ("NORMAL", "default")))
        return scope + (str(conflict["waypoint"]).upper(),) + tuple(sorted(
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
            existing = self._conflict_state.get(key)
            if existing is not None:
                if existing.get("status") in {"pending", "holding"}:
                    return False
                if monotonic() < existing.get("retry_after", 0.0):
                    return False
                # An unresolved detector-confirmed conflict with no applicable
                # hold must be replanned instead of being deduplicated forever.
                self._conflict_state.pop(key, None)
            self._conflict_state[key] = {
                "status": "pending", "callsigns": {trailing_callsign},
                "priority_callsign": priority_callsign,
                "trailing_callsign": trailing_callsign,
            }
            if mistral_client.client is None:
                result = self._fallback_locked(key, conflict, holding_manager, "Mistral client unavailable")
            elif len(self._futures) >= self.max_pending_decisions:
                result = self._fallback_locked(
                    key, conflict, holding_manager, "AI decision concurrency limit reached"
                )
            else:
                try:
                    options = {}
                    for aircraft in conflict.get("aircraft", []):
                        aircraft_options = holding_manager.compatible_holding_options(
                            aircraft, conflict["waypoint"]
                        )
                        enriched = []
                        for option in aircraft_options:
                            route = holding_manager._holding_route_by_id(option["holding_route_id"]) or {}
                            enriched.append({
                                **option,
                                "lower_limit": route.get("lower_limit"),
                                "upper_limit": route.get("upper_limit"),
                                "available_levels_ft": route.get("holding_levels_ft", []),
                                "max_speed_kt": route.get("max_speed_kt"),
                                "capacity": route.get("capacity"),
                            })
                        options[str(aircraft.callsign)] = enriched
                    conflict["compatible_options_by_callsign"] = options
                    assignments = []
                    for state in holding_manager.active_holding_routes():
                        state["occupancy"] = holding_manager.holding_occupancy(
                            state.get("holding_route_id")
                        )
                        route = holding_manager._holding_route_by_id(state.get("holding_route_id")) or {}
                        state["capacity"] = route.get("capacity")
                        assignments.append(state)
                    conflict["active_holding_assignments"] = assignments
                    prompt = build_conflict_prompt(conflict)
                    future = self._executor.submit(mistral_client.generate, prompt)
                    self._track_future(future)
                    self._pending[key] = {
                        "future": future, "started": monotonic(), "conflict": conflict,
                        "holding_manager": holding_manager, "callback": on_result,
                        "snapshot": self._snapshot(conflict),
                    }
                    return True
                except Exception as exc:
                    result = self._fallback_locked(key, conflict, holding_manager,
                                                   f"AI request could not start: {exc}")
        if on_result is not None and result is not None:
            on_result(result)
        return True

    @staticmethod
    def _snapshot(conflict):
        return {str(ac.callsign): (tuple(getattr(ac, "route", []) or []),
                                    getattr(ac, "route_index", None),
                                    float(getattr(ac, "lat", 0)), float(getattr(ac, "lon", 0)),
                                    float(getattr(ac, "altitude_ft", 0)),
                                    getattr(ac, "target_node", None), getattr(ac, "phase", None),
                                    bool(getattr(ac, "approach_clearance", False)))
                for ac in conflict.get("aircraft", [])}

    def _fallback_locked(self, key, conflict, holding_manager, reason):
        result = self._apply_deterministic_hold(conflict, holding_manager)
        result["decision_source"] = "deterministic_fallback"
        result["validation_result"] = "fallback"
        result["validation_reason"] = reason
        state = self._conflict_state.get(key, {})
        state["status"] = "holding" if result["holding"] else "unresolved"
        if result["holding"]:
            selected = result["selected_aircraft"]
            state["held_callsign"] = selected.callsign
            hold = holding_manager.state_for(selected)
            if hold is not None:
                hold["priority_callsign"] = conflict.get("priority_callsign")
                hold["protected_merge_point"] = conflict["waypoint"]
        else:
            state["retry_after"] = monotonic() + self.request_timeout_seconds
        return result

    def process_pending(self, current_conflicts, holding_manager, on_result):
        """Apply completed model advice on the simulation thread; time out to the safe fallback."""
        by_key = {self.conflict_key(item): item for item in current_conflicts}
        callbacks = []
        with self._lock:
            for key, pending in list(self._pending.items()):
                conflict = by_key.get(key)
                if conflict is None:
                    pending["future"].cancel()
                    pending["orphaned"] = True
                    if pending["future"].done():
                        self._pending.pop(key, None)
                        self._conflict_state.pop(key, None)
                    continue
                if pending.get("fallback_applied"):
                    if pending["future"].done():
                        self._pending.pop(key, None)
                    continue
                priority_callsign, trailing_callsign = self._priority_and_trailing_callsigns(conflict)
                conflict["priority_callsign"] = priority_callsign
                conflict["trailing_callsign"] = trailing_callsign
                future = pending["future"]
                timed_out = monotonic() - pending["started"] >= self.request_timeout_seconds
                if not future.done() and not timed_out:
                    continue
                reason = "AI decision timed out" if timed_out and not future.done() else "AI response unavailable or invalid"
                if future.done():
                    self._pending.pop(key, None)
                try:
                    response = future.result(timeout=0) if future.done() else None
                    decision = parse(response)
                    if decision is None:
                        raise ValueError(reason)
                    if not self._snapshot_is_current(pending["snapshot"], conflict, holding_manager):
                        raise ValueError("AI response rejected because traffic state changed materially")
                    if decision["decision"] != "HOLD":
                        raise ValueError("NONE cannot clear a detector-confirmed unsafe conflict")
                    decision_conflict = dict(conflict)
                    decision_conflict["trailing_callsign"] = conflict.get("trailing_callsign") or self._priority_and_trailing_callsigns(conflict)[1]
                    result = self._apply_model_hold(decision, decision_conflict, holding_manager)
                    result["decision_source"] = "Mistral"
                    result["validation_result"] = "accepted"
                    result["validation_reason"] = "Recommendation matched current conflict and holding constraints"
                    state = self._conflict_state.get(key, {})
                    state["status"] = "holding"
                    state["held_callsign"] = result["selected_aircraft"].callsign
                    priority_callsign = next(
                        (str(aircraft.callsign) for aircraft in conflict["aircraft"]
                         if aircraft is not result["selected_aircraft"]),
                        conflict.get("priority_callsign"),
                    )
                    hold = holding_manager.state_for(result["selected_aircraft"])
                    if hold is not None:
                        hold["priority_callsign"] = priority_callsign
                        hold["protected_merge_point"] = conflict["waypoint"]
                except Exception as exc:
                    result = self._fallback_locked(key, conflict, holding_manager, str(exc))
                    if not future.done():
                        pending["fallback_applied"] = True
                result["conflict"] = conflict
                callbacks.append((pending.get("callback") or on_result, result))
        for callback, result in callbacks:
            if callback is not None:
                callback(result)

    @staticmethod
    def _snapshot_is_current(snapshot, conflict, holding_manager):
        current = MistralController._snapshot(conflict)
        if set(snapshot) != set(current):
            return False
        for callsign, before in snapshot.items():
            after = current[callsign]
            mean_lat = (before[2] + after[2]) / 2.0
            moved_nm = (((after[2] - before[2]) * 60.0) ** 2
                        + ((after[3] - before[3]) * 60.0 * math.cos(
                            math.radians(mean_lat))) ** 2) ** 0.5
            if before[:2] != after[:2] or before[5:] != after[5:] or moved_nm > 2.0:
                return False
            if abs(before[4] - after[4]) > 1000 or holding_manager.has_active_holding_route(
                    next(ac for ac in conflict["aircraft"] if str(ac.callsign) == callsign)):
                return False
        return True

    @staticmethod
    def _apply_model_hold(decision, conflict, holding_manager):
        involved = {str(ac.callsign): ac for ac in conflict["aircraft"]}
        selected = involved.get(decision["callsign"])
        if selected is None:
            raise ValueError("Mistral selected an aircraft outside the detected conflict")
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
            selected, decision["holding_route_id"], circuits=1,
            preferred_level_ft=altitude,
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

    def clear_conflicts(self, detected_keys, active_holding_callsigns=(), session_scope=None):
        active_holding_callsigns = set(active_holding_callsigns)
        session_scope = tuple(session_scope) if session_scope is not None else None
        with self._lock:
            for key, state in list(self._conflict_state.items()):
                if session_scope is not None and tuple(key[:2]) != session_scope:
                    continue
                if (state.get("status") == "holding"
                        and state.get("held_callsign") not in active_holding_callsigns):
                    # Reopen the decision lifecycle once the intervention has
                    # actually ended. If the detector still sees the conflict,
                    # the next pass can request a new intervention.
                    del self._conflict_state[key]
                    continue
                if key in detected_keys:
                    continue
                if state.get("status") == "pending":
                    del self._conflict_state[key]
                    continue
                if state.get("held_callsign") in active_holding_callsigns:
                    continue
                del self._conflict_state[key]

    def release_for_callsign(self, callsign):
        with self._lock:
            for key, state in list(self._conflict_state.items()):
                if callsign in key[2:] or state.get("held_callsign") == callsign:
                    del self._conflict_state[key]

    def has_pending_conflict_for(self, callsign):
        with self._lock:
            return any(
                state.get("status") == "pending" and callsign in state.get("callsigns", set())
                for state in self._conflict_state.values()
            )

mistral_controller = MistralController()
