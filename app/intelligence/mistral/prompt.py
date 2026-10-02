import json


SYSTEM_PROMPT = """You make one Arrival ATC decision for an already detected shared arrival waypoint conflict.
Normal route guidance is deterministic. Decide only whether to intervene for this detected conflict.
Do not discover conflicts, invent routes or holding waypoints, modify permanent routes, sequence approaches, or decide runway order.
If separation is adequate, return decision NONE. Otherwise select exactly one of the two involved aircraft and a holding route supplied for that aircraft and conflict fix. Preserve route/procedure altitude and speed constraints unless a valid temporary value is required.
Return only a JSON object with exactly: controller, decision, callsign, instruction, holding_route_id, holding_fix, altitude, speed, reason.
For HOLD, controller is Arrival, callsign is one of the involved callsigns, holding_route_id is one of that aircraft's supplied compatible routes, and holding_fix is the conflict waypoint. altitude and speed may be null.
For NONE, use controller Arrival, decision NONE, callsign/holding_route_id/holding_fix/altitude/speed null, and instruction/reason empty strings."""


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
        for holding in holding_routes:
            if str(route.get("id")) not in {str(item) for item in holding.get("source_routes", [])}:
                continue
            if str(holding.get("trigger_waypoint", "")).upper() != str(conflict["waypoint"]).upper():
                continue
            compatible.append({
                "id": holding.get("id"),
                "fix": holding.get("fix", holding.get("trigger_waypoint")),
                "waypoints": holding.get("waypoints", []),
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
        "situation": "A deterministic detector has confirmed predicted traffic conflict at a shared arrival waypoint.",
        "conflict_waypoint": conflict["waypoint"],
        "active_runway": conflict.get("active_runway", "27"),
        "aircraft": [
            aircraft_state(first, routes[getattr(first, "arrival_route_id")], conflict["holding_routes"]),
            aircraft_state(second, routes[getattr(second, "arrival_route_id")], conflict["holding_routes"]),
        ],
    }
    return json.dumps(state, indent=2)


def build_prompt(aircraft, traffic, event):
    """Compatibility wrapper; conflict decisions should call build_conflict_prompt."""
    return json.dumps({"event": event, "callsign": aircraft.callsign})
