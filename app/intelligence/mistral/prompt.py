import json


SYSTEM_PROMPT = """You make one Arrival ATC decision for an already detected near-field arrival merge conflict.
Normal route following, waypoint constraints, aircraft movement, holding geometry, and route topology are deterministic. The detector derives shared fixes from loaded arrival routes and checks downstream approach timing against the configured approach path. Intervene only for a detected merge conflict or a predicted unsafe hold release.
Do not invent aircraft, waypoints, routes, holding routes, or holding fixes. Select a hold only from that aircraft's compatible_holding_patterns list. The selected holding_fix may be upstream of the conflict fix when the provided option says so. Do not modify permanent routes or decide runway order.
If separation is adequate, return decision NONE. Otherwise select one involved aircraft and one provided holding option; select valid temporary altitude/speed if needed and hold_circuits as an integer from 1 to 10. The deterministic simulator rechecks separation at each completed circuit and may extend the hold.
Return only JSON with exactly: controller, decision, callsign, instruction, holding_route_id, holding_fix, altitude, speed, hold_circuits, rejoin_node, reason. decision must be HOLD or NONE.
For HOLD, copy callsign, holding_route_id, holding_fix, and rejoin_node exactly from the selected aircraft's compatible_holding_patterns list. controller is Arrival. hold_circuits is an integer from 1 to 10. altitude/speed are null or usable numeric values.
For NONE, use controller Arrival, decision NONE, callsign/holding_route_id/holding_fix/altitude/speed/hold_circuits/rejoin_node null, and instruction/reason empty strings."""


def _waypoint_constraints(route, waypoint_name):
    for point in route.get("waypoints", []):
        if str(point.get("name", "")).upper() != str(waypoint_name).upper():
            continue
        altitude = point.get("altitude")
        speed = point.get("speed")
        return {
            "altitude": altitude.get("feet") if isinstance(altitude, dict) else altitude,
            "speed": speed.get("knots") if isinstance(speed, dict) else speed,
        }
    return {"altitude": None, "speed": None}


def build_conflict_prompt(conflict):
    def aircraft_state(aircraft, route, holding_routes):
        compatible = []
        route_names = [str(node).upper() for node in getattr(aircraft, "route", [])]
        route_index = getattr(aircraft, "route_index", 0)
        conflict_index = next((i for i in range(route_index, len(route_names))
                               if route_names[i] == str(conflict["waypoint"]).upper()), None)
        for holding in holding_routes:
            fix = str(holding.get("trigger_waypoint", ""))
            try:
                fix_index = next(i for i in range(route_index, len(route_names))
                                 if route_names[i] == fix.upper())
            except StopIteration:
                continue
            if conflict_index is None or fix_index > conflict_index:
                continue
            compatible.append({
                "id": holding.get("id"),
                "holding_route_id": holding.get("id"),
                "holding_fix": fix,
                "rejoin_node": route_names[fix_index + 1] if fix_index + 1 < len(route_names) else None,
                "fix": holding.get("fix", fix),
                "lower_limit": holding.get("lower_limit"),
                "upper_limit": holding.get("upper_limit"),
                "max_speed_kt": holding.get("max_speed_kt"),
                "turn_direction": holding.get("turn_direction"),
                "entry_turn_direction": holding.get("entry_turn_direction"),
                "inbound_course_true": holding.get("inbound_course_true"),
                "leg_length_nm": holding.get("leg_length_nm"),
                "waypoints": conflict.get("holding_geometries", {}).get(
                    holding.get("id"), holding.get("waypoints", [])
                ),
            })
        return {
            "callsign": aircraft.callsign,
            "type": aircraft.aircraft_type,
            "position": {"latitude": aircraft.lat, "longitude": aircraft.lon},
            "altitude": aircraft.altitude_ft,
            "speed": aircraft.speed_kts,
            "heading": aircraft.heading_deg,
            "phase": aircraft.phase,
            "route": list(aircraft.route),
            "route_index": aircraft.route_index,
            "target_waypoint": aircraft.target_node,
            "route_id": getattr(aircraft, "arrival_route_id", None),
            "current_target_constraints": _waypoint_constraints(route, aircraft.target_node),
            "shared_waypoint_constraints": _waypoint_constraints(route, conflict["waypoint"]),
            "route_constraints": [
                {
                    "waypoint": point.get("name"),
                    **_waypoint_constraints(route, point.get("name")),
                }
                for point in route.get("waypoints", [])
                if _waypoint_constraints(route, point.get("name")) != {"altitude": None, "speed": None}
            ],
            "compatible_holding_patterns": compatible,
        }

    first, second = conflict["aircraft"]
    routes = conflict["routes"]
    state = {
        "situation": "A deterministic detector has confirmed predicted traffic conflict near an arrival merge or sequencing fix.",
        "conflict_waypoint": conflict["waypoint"],
        "active_runway": conflict.get("active_runway", "27"),
        "downstream_traffic": conflict.get("traffic_context", []),
        "predicted_timing": conflict.get("predicted_timing", {}),
        "aircraft": [
            aircraft_state(first, routes[getattr(first, "arrival_route_id")], conflict["holding_routes"]),
            aircraft_state(second, routes[getattr(second, "arrival_route_id")], conflict["holding_routes"]),
        ],
    }
    return json.dumps(state, indent=2)


def build_prompt(aircraft, traffic, event):
    """Compatibility wrapper; conflict decisions should call build_conflict_prompt."""
    return json.dumps({"event": event, "callsign": aircraft.callsign})
