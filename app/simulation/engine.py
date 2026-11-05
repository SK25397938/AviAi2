
import json
import logging
import math
import random
import time
from threading import RLock
from pathlib import Path
from datetime import datetime

from app.simulation.manager import AircraftManager
from app.simulation.holding_manager import HoldingManager
from app.simulation.spawn import spawn_aircraft
from app.simulation.physics import move_aircraft, TURN_RATE_DEG_PER_SEC
from app.simulation.aircraft_db import AircraftDatabase
from app.simulation.traffic_manager import TrafficManager
from app.simulation.traffic_scheduler import TrafficScheduler
from app.aircraft.aircraft_type import AIRCRAFT_PERFORMANCE, get_aircraft_performance

from app.intelligence.arrival_manager import ArrivalManager
from app.intelligence.arrival_ai import ArrivalAI
from app.intelligence.guidance_ai import GuidanceAI
from app.intelligence.approach_ai import ApproachAI
from app.intelligence.landing_ai import LandingAI
from app.intelligence.trajectory import apply_arrival_separation

from app.intelligence.mistral.controller import mistral_controller

from app.navigation.runway import Runway
from app.navigation.centerline import build_centerline
from app.navigation.grid_builder import build_grid
from app.navigation.graph_builder import build_graph
from app.navigation.loader import (load_arrival_profiles, load_arrival_routes,
                                   load_departure_routes, load_departure_profiles,
                                   load_departure_destination_map)
from app.navigation.arrival_profile import (arrival_schedule_groundspeed,
                                            resolve_spawn_altitude,
                                            resolve_spawn_speed)
from app.navigation.departure_profile import (departure_speed_limit_gs,
                                              match_departure_profile)
from app.navigation.speed_conversion import (indicated_to_true_airspeed,
                                             true_to_indicated_airspeed)
from app.navigation.vectoring import heading_between
from app.config.separation import MIN_HORIZONTAL_SEPARATION_NM


class SimulationEngine:

    SEQUENCING_WINDOW_NM = 35.0
    MIN_MERGE_SEPARATION_NM = MIN_HORIZONTAL_SEPARATION_NM
    MIN_VERTICAL_SEPARATION_FT = 1000
    CONFLICT_WARNING_DISTANCE_NM = 6.0
    PREDICTION_LOOKAHEAD_SECONDS = 120.0
    SEQUENCING_DECISION_HORIZON_SECONDS = 600.0
    MAX_SEQUENCING_SPEED_DELTA_KTS = 10.0
    CRITICAL_SEPARATION_NM = 1.0

    def __init__(
        self,
        airport_lat=19.0887,
        airport_lon=72.8679,
        session_mode="NORMAL",
        session_id=None,
    ):

        self.session_mode = str(session_mode).upper()
        self.session_id = session_id
        self.normal_generation_allowed = lambda: True
        self._test_spawn_sequence = 0

        self.manager = AircraftManager()

        self.aircraft_db = AircraftDatabase(
            ":memory:" if self.session_mode == "TEST" else "data/simulation/aircraft.sqlite"
        )

        traffic_database_path = (":memory:" if self.session_mode == "TEST"
                                  else "data/simulation/traffic.sqlite")
        self.traffic_manager = TrafficManager(
            seed_database=self.session_mode != "TEST",
            database_path=traffic_database_path,
        )

        if self.session_mode == "TEST":
            self.scheduler = TrafficScheduler(initialize=False, database_path=":memory:")
        else:
            self.scheduler = TrafficScheduler()

        self.airport_lat = airport_lat
        self.airport_lon = airport_lon

        self.runway = Runway(
            ident="27",
            threshold_lat=airport_lat,
            threshold_lon=airport_lon,
            heading=270,
            length_nm=25
        )

        self.centerline = build_centerline(
            self.runway
        )

        self.grid = build_grid(
            self.centerline,
            self.runway
        )

        self.graph = build_graph(
            self.grid
        )

        self.arrival_routes = load_arrival_routes()
        self.arrival_profiles = load_arrival_profiles()
        self.departure_routes = load_departure_routes()
        self.departure_profiles = load_departure_profiles()
        self.departure_destination_map = load_departure_destination_map()
        self.departure_waypoints = {}
        self.shared_arrival_waypoint_groups = self._shared_arrival_waypoint_groups()
        self.shared_arrival_waypoints = {
            next((str(item[1].get("name")).strip() for item in group
                  if isinstance(item[1].get("name"), str) and item[1].get("name").strip()),
                 f"{group[0][3][0]:.6f},{group[0][3][1]:.6f}"): sorted({item[0] for item in group}, key=str)
            for group in self.shared_arrival_waypoint_groups
        }

        holding_routes_path = Path(__file__).resolve().parents[2] / "holding_routes.json"
        try:
            with holding_routes_path.open("r", encoding="utf-8") as holding_routes_file:
                holding_routes_data = json.load(holding_routes_file)
            if isinstance(holding_routes_data, dict):
                holding_routes_data = holding_routes_data.get("holding_routes", [])
            self.holding_routes = (
                holding_routes_data
                if isinstance(holding_routes_data, list)
                else []
            )
        except (OSError, json.JSONDecodeError):
            self.holding_routes = []

        self.holding_manager = HoldingManager(self.holding_routes, self.graph)
        # Entry reservations cover pending clearances and remain owned until
        # the aircraft passes its assigned entry fix. Clearance requests may
        # be evaluated by concurrent callers, so allocation and pruning share
        # this reentrant lock.
        self.approach_reservations = {}
        self.approach_reservation_status = {}
        self.ai_arrival_decisions_enabled = True
        self._approach_allocation_lock = RLock()
        self.ai_decision_log = []
        self.decision_audit_counts = {}
        self.departure_diagnostics = []
        self._last_separation_event_at = {}
        self._last_holding_release_diagnostic_at = {}

        for route in self.departure_routes.values():
            for waypoint in route["waypoints"]:
                self.departure_waypoints[waypoint["name"]] = waypoint
                self.graph.add_node(waypoint["name"], waypoint["latitude"], waypoint["longitude"])

        for route in self.arrival_routes.values():

            for waypoint in route["waypoints"]:

                self.graph.add_node(
                    waypoint["name"],
                    waypoint["latitude"],
                    waypoint["longitude"]
                )

        # MB398 is available only to routes that explicitly include it as a
        # compatible fallback; it is never inserted into a route by default.
        self.graph.add_node("MB398", 19.453401, 73.462572)
        fallback_waypoints_path = (
            Path(__file__).resolve().parents[2]
            / "data" / "navigation" / "VABB" / "waypoints.json"
        )
        try:
            with fallback_waypoints_path.open("r", encoding="utf-8") as file:
                fallback_waypoints = json.load(file).get("waypoints", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            fallback_waypoints = []
        for waypoint in fallback_waypoints:
            if (isinstance(waypoint, dict)
                    and str(waypoint.get("name", "")).upper() in {"MB398", "OLGUS"}
                    and waypoint.get("lat") is not None and waypoint.get("lon") is not None):
                self.graph.add_node(
                    str(waypoint["name"]).upper(),
                    float(waypoint["lat"]), float(waypoint["lon"]),
                )

        self.arrival = ArrivalManager(
            self.graph
        )

        self.arrival_ai = ArrivalAI()

        self.guidance = GuidanceAI(
            self.graph,
            self.holding_manager,
            self.arrival_routes,
            self._can_release_holding,
            self._record_hold_release,
            self.arrival_profiles,
            self._record_guidance_event,
            self._can_approach_handoff,
            self._choose_holding_level,
            self._validate_arrival_fallback,
        )

        self.approach_ai = ApproachAI(
            self.runway
        )
        self.holding_manager.route_continuation_validator = self._has_configured_holding_continuation

        self.landing_ai = LandingAI(
            self.runway,
            self.graph
        )

    def _decision_session_scope(self):
        """Unique simulation scope for decisions stored by the shared controller."""
        return (str(getattr(self, "session_mode", "NORMAL")),
                str(getattr(self, "session_id", None) or "default"))

    def _validate_arrival_fallback(self, aircraft, fallback_name):
        """Validate a global delay-fix transition before it is spliced into a route."""
        fallback_name = str(fallback_name).strip().upper()
        if fallback_name not in {"MB398", "OLGUS"}:
            return {"safe": False, "reason": "Fallback is not in the configured global candidate set"}
        route_definition = self.arrival_routes.get(getattr(aircraft, "arrival_route_id", None))
        if not isinstance(route_definition, dict):
            return {"safe": False, "reason": "Arrival route is unavailable"}
        trigger = str(route_definition.get("approach_trigger", "")).upper()
        trigger_point = self.graph.get_node(trigger) if trigger else None
        fallback_point = self.graph.get_node(fallback_name)
        if trigger_point is None or fallback_point is None:
            return {"safe": False, "reason": "Fallback or route trigger is missing from navigation data"}
        try:
            own_lat, own_lon = float(aircraft.lat), float(aircraft.lon)
            speed = float(getattr(aircraft, "target_speed_kts", None)
                          or getattr(aircraft, "speed_kts", 0))
            heading = float(getattr(aircraft, "heading_deg", 0)) % 360.0
            own_altitude = float(getattr(aircraft, "altitude_ft", 0))
            own_vertical_speed = float(getattr(aircraft, "vertical_speed_fpm", 0) or 0)
            fallback_lat, fallback_lon = float(fallback_point["lat"]), float(fallback_point["lon"])
            trigger_lat, trigger_lon = float(trigger_point["lat"]), float(trigger_point["lon"])
        except (TypeError, ValueError, KeyError, AttributeError):
            return {"safe": False, "reason": "Fallback transition geometry is incomplete"}
        if speed <= 0:
            return {"safe": False, "reason": "Aircraft speed is unavailable for fallback reachability"}

        def local_xy(latitude, longitude):
            mean_latitude = math.radians((own_lat + latitude) / 2.0)
            return ((longitude - own_lon) * 60.0 * math.cos(mean_latitude),
                    (latitude - own_latitude) * 60.0)

        own_latitude = own_lat
        fallback_xy = local_xy(fallback_lat, fallback_lon)
        trigger_xy = local_xy(trigger_lat, trigger_lon)
        legs = [(0.0, 0.0, *fallback_xy), (*fallback_xy, *trigger_xy)]
        leg_lengths = [math.hypot(leg[2] - leg[0], leg[3] - leg[1]) for leg in legs]
        if not leg_lengths or any(length < 0.05 or length > 35.0 for length in leg_lengths):
            return {"safe": False, "reason": "Fallback route legs are not within reachable geometry"}
        total_distance = sum(leg_lengths)
        turn_rate = math.radians(TURN_RATE_DEG_PER_SEC)
        turn_radius_nm = speed / 3600.0 / max(turn_rate, 1e-6)
        bearings = [math.degrees(math.atan2(leg[2] - leg[0], leg[3] - leg[1])) % 360.0
                    for leg in legs]
        turns = [abs((bearings[0] - heading + 180.0) % 360.0 - 180.0),
                 abs((bearings[1] - bearings[0] + 180.0) % 360.0 - 180.0)]
        if any(turn_radius_nm * math.radians(turn) > leg_lengths[index] + 1e-6
               for index, turn in enumerate(turns)):
            return {"safe": False, "reason": "Aircraft cannot make the fallback turn within the available leg"}

        duration = total_distance * 3600.0 / speed
        for other in self.manager.all():
            if (other is aircraft or getattr(other, "phase", "")
                    not in {"ARRIVAL", "FINAL", "DEPARTURE"}):
                continue
            try:
                other_speed = max(0.0, float(getattr(other, "speed_kts", 0) or 0))
                other_heading = math.radians(float(getattr(other, "heading_deg", 0) or 0))
                other_lat, other_lon = float(other.lat), float(other.lon)
                other_altitude = float(getattr(other, "altitude_ft", 0))
                other_vs = float(getattr(other, "vertical_speed_fpm", 0) or 0)
            except (TypeError, ValueError, AttributeError):
                return {"safe": False, "reason": "Nearby traffic state is incomplete"}
            other_x, other_y = local_xy(other_lat, other_lon)
            other_vx = other_speed / 3600.0 * math.sin(other_heading)
            other_vy = other_speed / 3600.0 * math.cos(other_heading)
            steps = min(120, max(1, int(math.ceil((duration + 60.0) / 15.0))) )
            for step in range(steps + 1):
                seconds = min(duration + 60.0, step * 15.0)
                travel_nm = min(total_distance, speed * seconds / 3600.0)
                if travel_nm <= leg_lengths[0]:
                    fraction = travel_nm / leg_lengths[0]
                    own_x, own_y = fallback_xy[0] * fraction, fallback_xy[1] * fraction
                else:
                    fraction = min(1.0, (travel_nm - leg_lengths[0]) / leg_lengths[1])
                    own_x = fallback_xy[0] + (trigger_xy[0] - fallback_xy[0]) * fraction
                    own_y = fallback_xy[1] + (trigger_xy[1] - fallback_xy[1]) * fraction
                other_dx = other_vx * seconds
                other_dy = other_vy * seconds
                other_x_t = other_x + other_dx
                other_y_t = other_y + other_dy
                separation_nm = math.hypot(
                    own_x - other_x_t, own_y - other_y_t,
                )
                vertical_separation = abs(
                    own_altitude + own_vertical_speed * seconds / 60.0
                    - other_altitude - other_vs * seconds / 60.0
                )
                if (separation_nm < self.MIN_MERGE_SEPARATION_NM
                        and vertical_separation < self.MIN_VERTICAL_SEPARATION_FT):
                    return {"safe": False, "reason": (
                        f"Fallback path predicts {separation_nm:.2f} NM / "
                        f"{vertical_separation:.0f} ft separation from {other.callsign}"
                    ), "other_aircraft": other.callsign,
                        "predicted_separation_nm": round(separation_nm, 2)}
        return {"safe": True, "distance_nm": round(total_distance, 2),
                "reason": "Fallback geometry and predicted traffic separation validated"}

    def spawn(
        self,
        aircraft_type,
        callsign,
        route_id
    ):

        if getattr(self, "session_mode", "NORMAL") == "TEST":
            raise RuntimeError("Scheduled aircraft spawning is disabled in TEST sessions")
        if not getattr(self, "normal_generation_allowed", lambda: True)():
            raise RuntimeError("Normal traffic generation is suspended")

        if route_id not in self.arrival_routes:

            raise ValueError(
                f"No arrival route found for {route_id}"
            )

        route_definition = self.arrival_routes[
            route_id
        ]

        waypoints = route_definition[
            "waypoints"
        ]

        first_waypoint = waypoints[0]

        aircraft = spawn_aircraft(
            callsign,
            aircraft_type,
            first_waypoint["latitude"],
            first_waypoint["longitude"]
        )

        route_nodes = [waypoint["name"] for waypoint in waypoints]
        aircraft.assign_route(route_nodes)
        aircraft.arrival_route_id = route_id
        self._initialize_arrival_route(aircraft, route_definition)

        self.manager.add(
            aircraft
        )

        return aircraft

    def test_spawn_nodes(self):
        """Return route-node starts whose route/profile continuation is unique."""
        candidates = {}
        for route_id, route in self.arrival_routes.items():
            points = route.get("waypoints", [])
            profiles = self.arrival_profiles.get(str(route_id), {})
            if not points or any(
                not isinstance(profiles.get(str(item.get("name", "")).upper()), dict)
                or not isinstance(profiles[str(item.get("name", "")).upper()].get("cum_nm"), (int, float))
                or not isinstance(profiles[str(item.get("name", "")).upper()].get("profile_altitude_ft"), (int, float))
                for item in points
            ):
                continue
            for index, point in enumerate(points[:-1]):
                name = str(point.get("name", "")).strip().upper()
                profile = profiles.get(name)
                if (not name or not isinstance(profile, dict)
                        or not isinstance(profile.get("profile_altitude_ft"), (int, float))):
                    continue
                if not isinstance(profile.get("cum_nm"), (int, float)):
                    continue
                constraint = profile.get("altitude")
                profiled_altitude = float(profile["profile_altitude_ft"])
                if isinstance(constraint, dict):
                    limit = float(constraint.get("feet", profiled_altitude))
                    if ((constraint.get("type") == "AT" and profiled_altitude != limit)
                            or (constraint.get("type") == "AT_OR_ABOVE" and profiled_altitude < limit)
                            or (constraint.get("type") == "AT_OR_BELOW" and profiled_altitude > limit)):
                        continue
                speed = resolve_spawn_speed(points, self.arrival_profiles, str(route_id),
                                            float(profile["cum_nm"]), 250.0)
                if (not speed.get("resolved") or not any(
                    speed["speed_kts"] >= get_aircraft_performance(kind)["minimum_arrival_speed_kts"]
                    for kind in AIRCRAFT_PERFORMANCE
                )):
                    continue
                continuation = tuple(str(item.get("name", "")).upper()
                                     for item in points[index:])
                candidates.setdefault(name, []).append((route_id, index, continuation))
        eligible = []
        for name, options in candidates.items():
            continuations = {item[2] for item in options}
            if len(continuations) != 1:
                continue
            route_id, index, _ = options[0]
            route = self.arrival_routes[route_id]
            point = route["waypoints"][index]
            profile = self.arrival_profiles[str(route_id)][name]
            eligible.append({"node": name, "route_id": str(route_id),
                             "latitude": point["latitude"], "longitude": point["longitude"],
                             "altitude_ft": profile["profile_altitude_ft"],
                             "speed_kts": profile.get("speed", {}).get("knots"),
                             "route_index": index})
        return sorted(eligible, key=lambda item: (item["node"], item["route_id"]))

    def spawn_test_aircraft(self, callsign, aircraft_type, node, route_id):
        if getattr(self, "session_mode", "NORMAL") != "TEST" or not getattr(self, "session_id", None):
            raise RuntimeError("Test spawning is not enabled")
        entry = next((item for item in self.test_spawn_nodes()
                      if item["node"] == str(node).upper()
                      and item["route_id"] == str(route_id)), None)
        if entry is None:
            raise ValueError("Node is not an eligible test spawn point")
        if any(ac.callsign == callsign for ac in self.manager.all()):
            raise ValueError("Callsign is already active in this test session")
        points = self.arrival_routes[entry["route_id"]]["waypoints"]
        profile = self.arrival_profiles[entry["route_id"]][entry["node"]]
        performance = get_aircraft_performance(aircraft_type)
        altitude = float(profile.get("profile_altitude_ft"))
        constraint = profile.get("altitude")
        if isinstance(constraint, dict):
            limit = float(constraint.get("feet", altitude))
            if constraint.get("type") == "AT":
                altitude = limit
            if ((constraint.get("type") == "AT_OR_ABOVE" and altitude < limit)
                    or (constraint.get("type") == "AT_OR_BELOW" and altitude > limit)):
                raise ValueError("Node profile conflicts with its published altitude constraint")
        speed_result = resolve_spawn_speed(
            points, self.arrival_profiles, entry["route_id"],
            float(profile["cum_nm"]), performance["spawn_speed_kts"]
        )
        if not speed_result.get("resolved"):
            raise ValueError("Node speed profile cannot be resolved safely")
        speed = speed_result["speed_kts"]
        speed = float(speed)
        if not math.isfinite(altitude) or altitude <= 0 or not math.isfinite(speed):
            raise ValueError("Node has invalid profile data")
        if speed < performance["minimum_arrival_speed_kts"]:
            raise ValueError("Aircraft type is incompatible with the node speed restriction")
        index = entry["route_index"]
        aircraft = spawn_aircraft(callsign, aircraft_type, entry["latitude"], entry["longitude"])
        aircraft.altitude_ft = round(altitude)
        aircraft.target_altitude_ft = round(altitude)
        aircraft.speed_kts = round(speed)
        aircraft.target_speed_kts = round(speed)
        aircraft.assign_route([item["name"] for item in points])
        aircraft.arrival_route_id = entry["route_id"]
        aircraft.test_session_id = self.session_id
        aircraft.test_spawned = True
        aircraft.phase = "ARRIVAL"
        aircraft.approach_phase = "ARRIVAL"
        aircraft.assigned_node = points[index]["name"]
        aircraft.route_index = index + 1
        aircraft.target_node = points[index + 1]["name"]
        next_point = points[index + 1]
        aircraft.heading_deg = heading_between(aircraft.lat, aircraft.lon,
                                                next_point["latitude"], next_point["longitude"])
        aircraft.target_heading_deg = aircraft.heading_deg
        aircraft.current_distance = 0.0
        self.manager.add(aircraft)
        return aircraft

    def spawn_random_test_aircraft(self, node, route_id):
        entry = next((item for item in self.test_spawn_nodes()
                      if item["node"] == str(node).upper()
                      and item["route_id"] == str(route_id)), None)
        if entry is None:
            raise ValueError("Node is not an eligible test spawn point")
        profile = self.arrival_profiles[entry["route_id"]][entry["node"]]
        speed = resolve_spawn_speed(
            self.arrival_routes[entry["route_id"]]["waypoints"], self.arrival_profiles,
            entry["route_id"], float(profile["cum_nm"]), 250.0
        )
        compatible = [kind for kind in AIRCRAFT_PERFORMANCE
                      if speed.get("resolved") and speed["speed_kts"] >=
                      get_aircraft_performance(kind)["minimum_arrival_speed_kts"]]
        if not compatible:
            raise ValueError("No aircraft type is compatible with this node profile")
        aircraft_type = random.choice(compatible)
        self._test_spawn_sequence += 1
        callsign = f"TST{self._test_spawn_sequence:04d}"
        return self.spawn_test_aircraft(callsign, aircraft_type, node, route_id)

    def remove_test_aircraft(self, callsign):
        if getattr(self, "session_mode", "NORMAL") != "TEST":
            raise ValueError("Test session is not active")
        aircraft = next((item for item in self.manager.all()
                         if item.callsign == callsign
                         and getattr(item, "test_session_id", None) == self.session_id), None)
        if aircraft is None:
            return False
        self.holding_manager.cancel_holding_route(aircraft, "test aircraft removed")
        self._release_approach_reservation(aircraft)
        self.manager.remove(aircraft)
        self._last_separation_event_at.pop(callsign, None)
        self._last_holding_release_diagnostic_at.pop(callsign, None)
        return True

    def spawn_due_arrivals(self, dt=1.0):

        if (getattr(self, "session_mode", "NORMAL") == "TEST"
                or not getattr(self, "normal_generation_allowed", lambda: True)()):
            return []

        active_aircraft = self.manager.all()
        wave_active_count = sum(
            1 for aircraft in active_aircraft
            if not (
                getattr(aircraft, "arrival_route_id", None) is not None
                and getattr(aircraft, "phase", "") == "FINAL"
            )
        )
        arrivals = self.traffic_manager.update(
            dt,
            can_release_arrival=self._arrival_has_spacing,
            active_aircraft_count=len(active_aircraft),
            wave_active_aircraft_count=wave_active_count,
        )

        active_callsigns = {
            aircraft.callsign
            for aircraft in self.manager.all()
        }
        spawned_this_update = []

        if not self.arrival_routes:
            return

        for index, aircraft_data in enumerate(arrivals):

            callsign = aircraft_data["callsign"]

            if callsign in active_callsigns:
                continue

            route_id = self._arrival_route_for(
                callsign,
                additional_aircraft=spawned_this_update
            )

            if route_id is None:
                continue

            # The traffic manager releases the initial batch together and does
            # not consult its spacing callback for that batch. Recheck here so
            # an initial arrival cannot occupy an already-used route entry.
            if not self._arrival_has_spacing(
                aircraft_data,
                route_id=route_id,
                additional_aircraft=spawned_this_update
            ):
                self.traffic_manager.mark_arrival_available(callsign)
                continue

            try:

                aircraft = self.spawn(
                    aircraft_type=aircraft_data["aircraft_type"],
                    callsign=callsign,
                    route_id=route_id
                )

                aircraft.arrival_route_id = route_id
                spawned_this_update.append(aircraft)

                print(
                    f"TRAFFIC SPAWNED: "
                    f"{callsign} | "
                    f"{aircraft_data['airline']} | "
                    f"{aircraft_data['origin']} -> "
                    f"{aircraft_data['destination']}"
                )

            except Exception as error:

                print(
                    f"ARRIVAL SPAWN ERROR "
                    f"{callsign}: {error}"
                )

    @staticmethod
    def _route_segment_length_nm(start, end):

        mean_lat = math.radians(
            (start["latitude"] + end["latitude"]) / 2
        )
        dx = (end["longitude"] - start["longitude"]) * 60.0 * math.cos(mean_lat)
        dy = (end["latitude"] - start["latitude"]) * 60.0
        return math.hypot(dx, dy)

    def _route_progress_nm(self, aircraft, route):

        if not isinstance(route, dict):
            return None

        points = route.get("waypoints", [])
        if len(points) < 2:
            return None

        progress = 0.0
        best_progress = None
        best_distance_sq = float("inf")
        lat = aircraft.lat
        lon = aircraft.lon

        for start, end in zip(points, points[1:]):
            mean_lat = math.radians(
                (start["latitude"] + end["latitude"]) / 2
            )
            lon_scale = 60.0 * math.cos(mean_lat)
            dx = (end["longitude"] - start["longitude"]) * lon_scale
            dy = (end["latitude"] - start["latitude"]) * 60.0
            px = (lon - start["longitude"]) * lon_scale
            py = (lat - start["latitude"]) * 60.0
            length_sq = dx * dx + dy * dy
            fraction = (
                0.0
                if length_sq == 0
                else max(0.0, min(1.0, (px * dx + py * dy) / length_sq))
            )
            distance_sq = (px - fraction * dx) ** 2 + (py - fraction * dy) ** 2
            segment_nm = math.sqrt(length_sq)

            if distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_progress = progress + fraction * segment_nm

            progress += segment_nm

        return best_progress

    def _route_distance_to_waypoint_nm(self, aircraft, route, waypoint_name, progress=None):
        points = route.get("waypoints", []) if isinstance(route, dict) else []
        if not points:
            return None
        if progress is None:
            progress = self._route_progress_nm(aircraft, route)
        if progress is None:
            return None
        travelled = 0.0
        for index, point in enumerate(points):
            if index:
                travelled += self._route_segment_length_nm(points[index - 1], point)
            if (str(point.get("name", "")).upper() == str(waypoint_name).upper()
                    and travelled >= progress - 0.1):
                return max(0.0, travelled - progress)
        return None

    def _active_route_distance_to_trigger_nm(self, aircraft, route, waypoint_name=None):
        """Measure from current position along its active route to a named fix."""
        active_route = getattr(aircraft, "route", None)
        route_points = route.get("waypoints", []) if isinstance(route, dict) else []
        trigger = str(waypoint_name or route.get("approach_trigger", "")).upper() if isinstance(route, dict) else ""
        if not isinstance(active_route, list) or not active_route or not route_points or not trigger:
            return None

        names = [str(name).upper() for name in active_route]
        target = str(getattr(aircraft, "target_node", "")).upper()
        route_index = getattr(aircraft, "route_index", 0)
        cursor = max(0, min(int(route_index or 0), len(names) - 1))
        target_indices = [index for index, name in enumerate(names) if name == target]
        active_target_index = next((index for index in target_indices if index >= cursor), None)
        if active_target_index is not None:
            start_index = active_target_index
        elif target_indices:
            start_index = target_indices[0]
        else:
            start_index = cursor
            if (getattr(self, "holding_manager", None) is not None
                    and self.holding_manager.has_active_holding_route(aircraft)):
                start_index = min(start_index + 1, len(names) - 1)
        end_index = next((index for index in range(start_index, len(names))
                          if names[index] == trigger), None)
        if end_index is None:
            return None

        coordinates = {
            str(point.get("name", "")).upper(): (point.get("latitude"), point.get("longitude"))
            for point in route_points if isinstance(point, dict)
        }
        graph = getattr(self, "graph", None)
        if graph is not None:
            for name, node in graph.nodes.items():
                coordinates[str(name).upper()] = (node.get("lat"), node.get("lon"))

        def point_for(name):
            point = coordinates.get(name)
            if point is None or point[0] is None or point[1] is None:
                return None
            return {"latitude": float(point[0]), "longitude": float(point[1])}

        target_point = point_for(names[start_index])
        if target_point is None:
            return None
        distance = self._route_segment_length_nm(
            {"latitude": float(aircraft.lat), "longitude": float(aircraft.lon)}, target_point
        )
        for index in range(start_index + 1, end_index + 1):
            previous, current = point_for(names[index - 1]), point_for(names[index])
            if previous is None or current is None:
                return None
            distance += self._route_segment_length_nm(previous, current)
        return distance

    def _approach_merge_eta_seconds(self, aircraft, route, progress, speed):
        points = route.get("waypoints", []) if isinstance(route, dict) else []
        approach_points = getattr(self.approach_ai, "approach_points", [])
        if len(points) < 2 or not approach_points or speed <= 0:
            return None

        if getattr(aircraft, "phase", "") == "FINAL":
            index = getattr(aircraft, "approach_index", None)
            if not isinstance(index, int) or index >= len(approach_points):
                return None
            current = {"latitude": aircraft.lat, "longitude": aircraft.lon}
            distance = self._route_segment_length_nm(current, approach_points[index])
            for previous, following in zip(approach_points[index:], approach_points[index + 1:]):
                distance += self._route_segment_length_nm(previous, following)
            return distance * 3600.0 / speed

        active_remaining = self._active_route_distance_to_trigger_nm(aircraft, route)
        if active_remaining is not None:
            remaining = active_remaining
            endpoint = next((point for point in points
                             if str(point.get("name", "")).upper()
                             == str(route.get("approach_trigger", "")).upper()), points[-1])
        else:
            total_route_distance = sum(
            self._route_segment_length_nm(first, second)
            for first, second in zip(points, points[1:])
            )
            remaining = max(0.0, total_route_distance - progress)
            endpoint = points[-1]
        intercept_index = min(
            range(len(approach_points)),
            key=lambda index: self._route_segment_length_nm(endpoint, approach_points[index]),
        )
        remaining += self._route_segment_length_nm(endpoint, approach_points[intercept_index])
        for index in range(intercept_index, len(approach_points) - 1):
            remaining += self._route_segment_length_nm(approach_points[index], approach_points[index + 1])
        return remaining * 3600.0 / speed

    def _approach_point_etas(self, aircraft, route, progress, speed):
        """Estimate arrival time at each downstream fix on the shared approach path."""
        route_points = route.get("waypoints", []) if isinstance(route, dict) else []
        approach_points = getattr(self.approach_ai, "approach_points", [])
        if not route_points or not approach_points or speed <= 0:
            return {}
        if getattr(aircraft, "phase", "") == "FINAL":
            start_index = getattr(aircraft, "approach_index", None)
            if not isinstance(start_index, int) or start_index >= len(approach_points):
                return {}
            start = {"latitude": aircraft.lat, "longitude": aircraft.lon}
            distance = self._route_segment_length_nm(start, approach_points[start_index])
            result = {}
            for index in range(start_index, len(approach_points)):
                if index > start_index:
                    distance += self._route_segment_length_nm(approach_points[index - 1], approach_points[index])
                result[index] = distance * 3600.0 / speed
            return result

        active_remaining = self._active_route_distance_to_trigger_nm(aircraft, route)
        if active_remaining is None:
            travelled_to_end = sum(self._route_segment_length_nm(a, b)
                                   for a, b in zip(route_points, route_points[1:]))
            endpoint = route_points[-1]
            remaining_distance = max(0.0, travelled_to_end - progress)
        else:
            remaining_distance = active_remaining
            endpoint = next((point for point in route_points
                             if str(point.get("name", "")).upper()
                             == str(route.get("approach_trigger", "")).upper()), route_points[-1])
        start_index = min(range(len(approach_points)),
                          key=lambda index: self._route_segment_length_nm(endpoint, approach_points[index]))
        distance = remaining_distance
        distance += self._route_segment_length_nm(endpoint, approach_points[start_index])
        result = {}
        for index in range(start_index, len(approach_points)):
            if index > start_index:
                distance += self._route_segment_length_nm(approach_points[index - 1], approach_points[index])
            result[index] = distance * 3600.0 / speed
        return result

    def _apply_merge_speed_delay(self, aircraft, distance_to_merge_nm, leader_eta_seconds, max_speed):
        target = getattr(aircraft, "target_speed_kts", None)
        minimum_speed = self._minimum_sequencing_speed(aircraft)
        if not isinstance(target, (int, float)) or target <= minimum_speed or distance_to_merge_nm <= 0:
            return False
        safe_gap_seconds = self.MIN_MERGE_SEPARATION_NM * 3600.0 / max_speed
        desired_eta = leader_eta_seconds + safe_gap_seconds
        if desired_eta <= 0:
            return False
        required_speed = distance_to_merge_nm * 3600.0 / desired_eta
        minimum_sequencing_target = max(
            minimum_speed, float(target) - self.MAX_SEQUENCING_SPEED_DELTA_KTS
        )
        if required_speed < minimum_sequencing_target or required_speed >= target - 1.0:
            return False
        if not hasattr(aircraft, "_engine_shared_waypoint_original_target_speed"):
            aircraft._engine_shared_waypoint_original_target_speed = target
        aircraft.assign_speed(required_speed)
        aircraft._sequence_speed_ceiling = required_speed
        aircraft._engine_shared_waypoint_applied_target_speed = required_speed
        return True

    def _minimum_sequencing_speed(self, aircraft):
        """Return a type- and phase-appropriate speed floor for sequencing."""
        performance = get_aircraft_performance(getattr(aircraft, "aircraft_type", ""))
        altitude = float(getattr(aircraft, "altitude_ft", 0) or 0)
        minimum = indicated_to_true_airspeed(
            float(performance.get("minimum_arrival_speed_kts", 170)), altitude
        )
        if getattr(aircraft, "phase", "") == "FINAL":
            index = getattr(aircraft, "approach_index", None)
            points = self.approach_ai.approach_points
            if isinstance(index, int) and 0 <= index < len(points):
                profile_speed = self.approach_ai._get_speed(aircraft, points[index].get("id"))
                if isinstance(profile_speed, (int, float)):
                    profile_speed = indicated_to_true_airspeed(
                        profile_speed, points[index].get("altitude_ft", altitude)
                    )
                    # Permit modest sequencing reduction while retaining a
                    # floor tied to this aircraft's configured approach speed.
                    minimum = max(135.0, min(minimum, float(profile_speed) - 10.0))
        return minimum

    @staticmethod
    def _relative_closure(first, second):
        """Horizontal range and positive closure rate, in NM and knots."""
        mean_latitude = math.radians((float(first.lat) + float(second.lat)) / 2.0)
        east = (float(second.lon) - float(first.lon)) * 60.0 * math.cos(mean_latitude)
        north = (float(second.lat) - float(first.lat)) * 60.0
        distance = math.hypot(east, north)
        if distance <= 1e-6:
            return distance, 0.0
        first_speed = float(getattr(first, "speed_kts", 0) or 0)
        second_speed = float(getattr(second, "speed_kts", 0) or 0)
        first_heading = math.radians(float(getattr(first, "heading_deg", 0) or 0))
        second_heading = math.radians(float(getattr(second, "heading_deg", 0) or 0))
        relative_east = second_speed * math.sin(second_heading) - first_speed * math.sin(first_heading)
        relative_north = second_speed * math.cos(second_heading) - first_speed * math.cos(first_heading)
        closure = -(east * relative_east + north * relative_north) / distance
        return distance, closure

    def _initialize_arrival_route(self, aircraft, route):
        """Initialize an arrival at route.waypoints[0], preserving route order."""
        waypoints = route.get("waypoints", [])
        if not waypoints:
            raise ValueError("Arrival route has no waypoints")

        first = waypoints[0]
        aircraft.lat = float(first["latitude"])
        aircraft.lon = float(first["longitude"])
        aircraft.route = [point["name"] for point in waypoints]
        aircraft.route_index = 0
        aircraft.assigned_node = aircraft.route[0]
        # The first waypoint is the initial target and is captured by normal
        # navigation on the first update before the aircraft heads to waypoint 1.
        aircraft.target_node = aircraft.route[0]
        aircraft._arrival_start_fix_pending = True

        route_profiles = self.arrival_profiles.get(str(aircraft.arrival_route_id), {})
        fallback = int(aircraft.altitude_ft)
        resolved = resolve_spawn_altitude(
            aircraft.arrival_route_id, waypoints, self.arrival_profiles,
            aircraft.lat, aircraft.lon, fallback,
        )
        if resolved["resolved"]:
            aircraft.altitude_ft = resolved["altitude_ft"]
            aircraft.target_altitude_ft = resolved["altitude_ft"]
        else:
            logging.getLogger(__name__).warning(
                "Arrival route profile unresolved route=%s waypoint=%s: %s; "
                "using documented aircraft performance spawn altitude %s ft",
                aircraft.arrival_route_id, resolved.get("waypoint"),
                resolved.get("reason"), fallback)

        scheduled_speed = arrival_schedule_groundspeed(
            getattr(aircraft, "aircraft_type", "A320"), aircraft.altitude_ft
        )
        first_fix_profile = route_profiles.get(str(first["name"]).upper(), {})
        first_fix_constraint = first_fix_profile.get("speed", {})
        if isinstance(first_fix_constraint, dict):
            first_fix_speed = first_fix_constraint.get("knots")
            if isinstance(first_fix_speed, (int, float)) and first_fix_speed > 0:
                speed_at_fix = indicated_to_true_airspeed(first_fix_speed, aircraft.altitude_ft)
                scheduled_speed = (min(scheduled_speed, speed_at_fix)
                                   if first_fix_constraint.get("type") == "MAX" else speed_at_fix)
        aircraft.target_speed_kts = scheduled_speed

        print(f"ARRIVAL SPAWN: {aircraft.callsign} route={aircraft.arrival_route_id} "
              f"spawn={first['name']} index=0 lat={aircraft.lat} lon={aircraft.lon} "
              f"alt={aircraft.altitude_ft} speed={aircraft.speed_kts} "
              f"profile_progress_nm={resolved.get('progress_nm')} "
              f"target_speed={aircraft.target_speed_kts}")

    def _arrival_route_for(self, callsign, additional_aircraft=()):

        route_ids = list(self.arrival_routes)
        if not route_ids:
            return None

        active_aircraft = list(self.manager.all())
        active_ids = {id(aircraft) for aircraft in active_aircraft}
        active_aircraft.extend(
            aircraft
            for aircraft in additional_aircraft
            if id(aircraft) not in active_ids
        )

        route_loads = {
            route_id: sum(
                1
                for aircraft in active_aircraft
                if getattr(aircraft, "arrival_route_id", None) == route_id
            )
            for route_id in route_ids
        }

        lowest_load = min(route_loads.values())
        least_loaded = [route_id for route_id, load in route_loads.items() if load == lowest_load]
        return random.choice(least_loaded)

    def _arrival_entry(self, route_id):

        route = self.arrival_routes.get(route_id)
        if not isinstance(route, dict):
            return None, None

        waypoints = route.get("waypoints", [])
        if not waypoints or not isinstance(waypoints[0], dict):
            return None, None

        first_waypoint = waypoints[0]
        name = first_waypoint.get("name")
        try:
            coordinates = (
                float(first_waypoint["latitude"]),
                float(first_waypoint["longitude"])
            )
        except (KeyError, TypeError, ValueError):
            coordinates = None

        if isinstance(name, str) and name.strip():
            return ("name", name.strip().upper()), coordinates
        if coordinates is None or not all(math.isfinite(value) for value in coordinates):
            return None, None
        return ("coordinates", round(coordinates[0], 6), round(coordinates[1], 6)), coordinates

    def _arrival_has_spacing(
        self,
        candidate,
        route_id=None,
        additional_aircraft=()
    ):

        if route_id is None:
            route_id = getattr(candidate, "arrival_route_id", None)
        if route_id is None:
            callsign = candidate.get("callsign") if isinstance(candidate, dict) else candidate
            route_id = self._arrival_route_for(callsign)
        if route_id is None:
            return True

        entry_key, entry_coordinates = self._arrival_entry(route_id)
        if entry_key is None or entry_coordinates is None:
            return True

        aircraft_on_route = list(self.manager.all())
        active_ids = {id(aircraft) for aircraft in aircraft_on_route}
        aircraft_on_route.extend(
            aircraft
            for aircraft in additional_aircraft
            if id(aircraft) not in active_ids
        )

        entry_point = {
            "latitude": entry_coordinates[0],
            "longitude": entry_coordinates[1]
        }
        for aircraft in aircraft_on_route:
            distance_nm = self._route_segment_length_nm(
                entry_point,
                {"latitude": aircraft.lat, "longitude": aircraft.lon}
            )
            if distance_nm + 1e-9 < self.MIN_MERGE_SEPARATION_NM:
                return False

        return True

    def _shared_arrival_waypoint_groups(self):

        occurrences = []
        for route_id, route in self.arrival_routes.items():
            if not isinstance(route, dict):
                continue

            points = route.get("waypoints", [])
            if not isinstance(points, list) or len(points) < 2:
                continue

            try:
                coordinates = [
                    (float(point["latitude"]), float(point["longitude"]))
                    for point in points
                ]
            except (KeyError, TypeError, ValueError):
                continue

            if not all(
                math.isfinite(value)
                for coordinate in coordinates
                for value in coordinate
            ):
                continue

            progress = 0.0
            for index, waypoint in enumerate(points):
                if index:
                    progress += self._route_segment_length_nm(
                        {
                            "latitude": coordinates[index - 1][0],
                            "longitude": coordinates[index - 1][1]
                        },
                        {
                            "latitude": coordinates[index][0],
                            "longitude": coordinates[index][1]
                        }
                    )
                occurrences.append((
                    route_id,
                    waypoint,
                    progress,
                    coordinates[index]
                ))

        parents = list(range(len(occurrences)))

        def find(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(first, second):
            first_root = find(first)
            second_root = find(second)
            if first_root != second_root:
                parents[second_root] = first_root

        for first in range(len(occurrences)):
            first_route, first_point, _, first_coordinates = occurrences[first]
            for second in range(first + 1, len(occurrences)):
                second_route, second_point, _, second_coordinates = occurrences[second]
                if first_route == second_route:
                    continue

                first_name = first_point.get("name")
                second_name = second_point.get("name")
                same_name = (
                    isinstance(first_name, str)
                    and isinstance(second_name, str)
                    and first_name.strip().upper() == second_name.strip().upper()
                )
                same_position = self._route_segment_length_nm(
                    {
                        "latitude": first_coordinates[0],
                        "longitude": first_coordinates[1]
                    },
                    {
                        "latitude": second_coordinates[0],
                        "longitude": second_coordinates[1]
                    }
                ) <= 0.05
                if same_name or same_position:
                    union(first, second)

        grouped = {}
        for index, occurrence in enumerate(occurrences):
            grouped.setdefault(find(index), []).append(occurrence)

        return [
            group
            for group in grouped.values()
            if len({occurrence[0] for occurrence in group}) > 1
        ]

    def _detect_approach_merge_conflicts(self, aircraft_list):
        arrivals = []
        for aircraft in aircraft_list:
            route_id = getattr(aircraft, "arrival_route_id", None)
            route = self.arrival_routes.get(route_id)
            if (not isinstance(route, dict)
                    or getattr(aircraft, "phase", "") not in ("ARRIVAL", "FINAL")
                    or self.holding_manager.has_active_holding_route(aircraft)
                    or getattr(aircraft, "separation_status", None) == "APPROACH DELAYED"
                    ):
                continue
            progress = self._route_progress_nm(aircraft, route)
            speed = getattr(aircraft, "target_speed_kts", None) or getattr(aircraft, "speed_kts", None)
            if progress is None or not isinstance(speed, (int, float)) or speed <= 0:
                continue
            point_etas = self._approach_point_etas(aircraft, route, progress, float(speed))
            if point_etas:
                arrivals.append((aircraft, route_id, route, progress, float(speed), point_etas))
        conflicts = []
        for first_index, first in enumerate(arrivals):
            for second in arrivals[first_index + 1:]:
                common_points = set(first[5]).intersection(second[5])
                candidates = []
                for point_index in common_points:
                    first_eta, second_eta = first[5][point_index], second[5][point_index]
                    if max(first_eta * first[4] / 3600.0,
                           second_eta * second[4] / 3600.0) > self.SEQUENCING_WINDOW_NM:
                        continue
                    first_altitude = (float(getattr(first[0], "altitude_ft", 0))
                                      + float(getattr(first[0], "vertical_speed_fpm", 0)) * first_eta / 60.0)
                    second_altitude = (float(getattr(second[0], "altitude_ft", 0))
                                       + float(getattr(second[0], "vertical_speed_fpm", 0)) * second_eta / 60.0)
                    gap_nm = abs(first_eta - second_eta) * max(first[4], second[4]) / 3600.0
                    if gap_nm < self.MIN_MERGE_SEPARATION_NM:
                        candidates.append((max(first_eta, second_eta), point_index,
                                           first_eta, second_eta, gap_nm))
                if not candidates:
                    continue
                _, approach_index, first_eta, second_eta, gap_nm = min(candidates)
                if max(first_eta, second_eta) > self.SEQUENCING_DECISION_HORIZON_SECONDS:
                    continue
                delayed = first if first_eta >= second_eta else second
                leader_eta = second_eta if delayed is first else first_eta
                delayed_eta = first_eta if delayed is first else second_eta
                leader = second if delayed is first else first
                current_separation, closure_rate = self._relative_closure(leader[0], delayed[0])
                horizons = (30, 60, 90, 120)
                projected_horizons = {
                    str(seconds): round(max(0.0, current_separation - closure_rate * seconds / 3600.0), 2)
                    for seconds in horizons
                }
                delayed_route = delayed[2]
                route_points = delayed_route.get("waypoints", [])
                endpoint_name = route_points[-1].get("name") if route_points else None
                distance_to_merge = delayed_eta * delayed[4] / 3600.0
                if (distance_to_merge > 0.1 and
                        self._apply_merge_speed_delay(delayed[0], distance_to_merge,
                                                      leader_eta, max(first[4], second[4]))):
                    self._record_separation_event(
                        delayed[0], leader[0], self.approach_ai.approach_points[approach_index].get("id"),
                        "SPEED REDUCTION",
                        f"ETA separation predicts {gap_nm:.2f} NM at downstream fix; "
                        f"current spacing {current_separation:.2f} NM, closure {closure_rate:.1f} kt",
                    )
                    continue
                if not endpoint_name:
                    continue
                timing = {
                    str(item[0].callsign): {
                        "route_id": item[1], "route_index": getattr(item[0], "route_index", None),
                        "current_waypoint": getattr(item[0], "target_node", None),
                        "distance_to_fix_nm": self._route_distance_to_waypoint_nm(
                            item[0], item[2], item[2].get("waypoints", [{}])[-1].get("name"), item[3]
                        ),
                        "eta_to_fix_seconds": first_eta if item is first else second_eta,
                        "altitude_ft": getattr(item[0], "altitude_ft", None),
                        "target_altitude_ft": getattr(item[0], "target_altitude_ft", None),
                        "speed_kts": getattr(item[0], "speed_kts", None),
                        "target_speed_kts": getattr(item[0], "target_speed_kts", None),
                        "heading_deg": getattr(item[0], "heading_deg", None),
                    } for item in (first, second)
                }
                conflicts.append({
                    "waypoint": endpoint_name,
                    "aircraft": (first[0], second[0]),
                    "routes": self.arrival_routes,
                    "holding_routes": self.holding_routes,
                    "holding_geometries": {
                        str(pattern.get("id")): self.holding_manager.racetrack_waypoints(pattern)
                        for pattern in self.holding_routes if isinstance(pattern, dict)
                    },
                    "active_runway": self.runway.ident,
                    "predicted_timing": {
                        "first_seconds_to_waypoint": round(first_eta, 1),
                        "second_seconds_to_waypoint": round(second_eta, 1),
                        "predicted_separation_nm": round(gap_nm, 2),
                        "merge_waypoint": self.approach_ai.approach_points[approach_index].get("id"),
                        "aircraft_ahead": str(leader[0].callsign),
                        "aircraft_behind": str(delayed[0].callsign),
                        "current_separation_nm": round(current_separation, 2),
                        "relative_closure_rate_kts": round(closure_rate, 1),
                        "time_to_required_separation_seconds": (
                            round(max(0.0, current_separation - self.MIN_MERGE_SEPARATION_NM)
                                  * 3600.0 / closure_rate, 1) if closure_rate > 0 else None
                        ),
                        "eta_ahead_at_merge_seconds": round(min(first_eta, second_eta), 1),
                        "eta_behind_at_merge_seconds": round(max(first_eta, second_eta), 1),
                        "projected_separation_nm_by_horizon": projected_horizons,
                        "prediction_horizon_seconds": max(horizons),
                    },
                    "required_separation_nm": self.MIN_MERGE_SEPARATION_NM,
                    "aircraft_timing": timing,
                    "traffic_context": [
                        {"callsign": other.callsign,
                         "position": {"latitude": other.lat, "longitude": other.lon},
                         "altitude": other.altitude_ft, "speed": other.speed_kts,
                         "heading": other.heading_deg, "route_id": getattr(other, "arrival_route_id", None),
                         "route": list(getattr(other, "route", [])),
                         "target_waypoint": getattr(other, "target_node", None)}
                        for other in aircraft_list
                        if other not in (first[0], second[0])
                        and getattr(other, "phase", "") in ("ARRIVAL", "FINAL")
                    ],
                })
        return conflicts

    def _detect_shared_waypoint_conflicts(self, aircraft_list):

        shared_waypoint_groups = self.shared_arrival_waypoint_groups
        detected_conflicts = []

        arrivals = []
        for aircraft in aircraft_list:
            route_id = getattr(aircraft, "arrival_route_id", None)
            if (
                getattr(aircraft, "phase", "") not in ("ARRIVAL", "FINAL")
                or route_id not in self.arrival_routes
                or self.holding_manager.has_active_holding_route(aircraft)
            ):
                continue

            route = self.arrival_routes[route_id]
            try:
                progress = self._route_progress_nm(aircraft, route)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            speed = getattr(aircraft, "speed_kts", None)
            target_speed = getattr(aircraft, "target_speed_kts", speed)
            if (
                progress is None
                or not isinstance(speed, (int, float))
                or not math.isfinite(speed)
                or speed <= 0
            ):
                continue

            effective_speed = (float(speed) + float(target_speed)) / 2.0 if isinstance(target_speed, (int, float)) and target_speed > 0 else float(speed)
            arrivals.append((aircraft, route_id, progress, effective_speed))

        arrivals.sort(key=lambda arrival: str(arrival[0].callsign))
        held_this_update = set()

        for group in shared_waypoint_groups:
            for first_index in range(len(arrivals)):
                first = arrivals[first_index]
                if id(first[0]) in held_this_update:
                    continue

                for second_index in range(first_index + 1, len(arrivals)):
                    second = arrivals[second_index]
                    if (
                        id(second[0]) in held_this_update
                        or self.holding_manager.has_active_holding_route(first[0])
                        or self.holding_manager.has_active_holding_route(second[0])
                    ):
                        continue

                    first_occurrences = sorted(
                        (
                            occurrence
                            for occurrence in group
                            if occurrence[0] == first[1]
                            and occurrence[2] - first[2] > 0
                        ),
                        key=lambda occurrence: occurrence[2] - first[2]
                    )
                    second_occurrences = sorted(
                        (
                            occurrence
                            for occurrence in group
                            if occurrence[0] == second[1]
                            and occurrence[2] - second[2] > 0
                        ),
                        key=lambda occurrence: occurrence[2] - second[2]
                    )
                    if not first_occurrences or not second_occurrences:
                        continue

                    for first_occurrence in first_occurrences:
                        first_remaining = first_occurrence[2] - first[2]
                        first_remaining_active = self._active_route_distance_to_trigger_nm(
                            first[0], self.arrival_routes[first[1]], first_occurrence[1].get("name")
                        )
                        if first_remaining_active is not None:
                            first_remaining = first_remaining_active
                        first_eta_seconds = first_remaining * 3600.0 / first[3]

                        for second_occurrence in second_occurrences:
                            first_name = first_occurrence[1].get("name")
                            second_name = second_occurrence[1].get("name")
                            same_name = (
                                isinstance(first_name, str)
                                and isinstance(second_name, str)
                                and first_name.strip().upper() == second_name.strip().upper()
                            )
                            same_position = self._route_segment_length_nm(
                                {
                                    "latitude": first_occurrence[3][0],
                                    "longitude": first_occurrence[3][1]
                                },
                                {
                                    "latitude": second_occurrence[3][0],
                                    "longitude": second_occurrence[3][1]
                                }
                            ) <= 0.05
                            if not (same_name or same_position):
                                continue

                            second_remaining = second_occurrence[2] - second[2]
                            second_remaining_active = self._active_route_distance_to_trigger_nm(
                                second[0], self.arrival_routes[second[1]], second_occurrence[1].get("name")
                            )
                            if second_remaining_active is not None:
                                second_remaining = second_remaining_active
                            if max(first_remaining, second_remaining) > self.SEQUENCING_WINDOW_NM:
                                continue
                            second_eta_seconds = second_remaining * 3600.0 / second[3]
                            prediction_time = max(first_eta_seconds, second_eta_seconds)
                            if prediction_time > self.SEQUENCING_DECISION_HORIZON_SECONDS:
                                continue
                            first_altitude = (float(getattr(first[0], "altitude_ft", 0))
                                              + float(getattr(first[0], "vertical_speed_fpm", 0)) * prediction_time / 60.0)
                            second_altitude = (float(getattr(second[0], "altitude_ft", 0))
                                               + float(getattr(second[0], "vertical_speed_fpm", 0)) * prediction_time / 60.0)
                            vertical_separation = abs(first_altitude - second_altitude)
                            fastest_speed = max(first[3], second[3])
                            arrival_gap_nm = (
                                abs(first_eta_seconds - second_eta_seconds)
                                * fastest_speed
                                / 3600.0
                            )
                            if arrival_gap_nm >= self.MIN_MERGE_SEPARATION_NM:
                                continue

                            first_waypoint = first_name
                            second_waypoint = second_name
                            if not first_waypoint or not second_waypoint:
                                continue

                            first_timing = (
                                first_eta_seconds,
                                first_remaining,
                                str(first[1]),
                                str(first[0].callsign)
                            )
                            second_timing = (
                                second_eta_seconds,
                                second_remaining,
                                str(second[1]),
                                str(second[0].callsign)
                            )
                            delayed, delayed_waypoint = (
                                (first, first_waypoint)
                                if first_timing > second_timing
                                else (second, second_waypoint)
                            )
                            delayed_eta = first_eta_seconds if delayed is first else second_eta_seconds
                            leader_eta = second_eta_seconds if delayed is first else first_eta_seconds
                            if self._apply_merge_speed_delay(
                                delayed[0], delayed_eta * delayed[3] / 3600.0,
                                leader_eta, fastest_speed
                            ):
                                self._record_separation_event(
                                    delayed[0], first[0] if delayed is second else second[0],
                                    delayed_waypoint, "SPEED REDUCTION",
                                    "Predicted merge spacing was below the configured minimum",
                                )
                                held_this_update.update((id(first[0]), id(second[0])))
                                break
                            detected_conflicts.append({
                                "waypoint": delayed_waypoint,
                                "aircraft": (first[0], second[0]),
                                "routes": self.arrival_routes,
                                "holding_routes": self.holding_routes,
                                "holding_geometries": {
                                    str(pattern.get("id")): self.holding_manager.racetrack_waypoints(pattern)
                                    for pattern in self.holding_routes
                                    if isinstance(pattern, dict)
                                },
                                "active_runway": self.runway.ident,
                                "predicted_timing": {
                                    "first_seconds_to_waypoint": round(first_eta_seconds, 1),
                                    "second_seconds_to_waypoint": round(second_eta_seconds, 1),
                                    "predicted_separation_nm": round(arrival_gap_nm, 2),
                                },
                                "required_separation_nm": self.MIN_MERGE_SEPARATION_NM,
                                "aircraft_timing": {
                                    str(first[0].callsign): {
                                        "route_id": first[1],
                                        "current_waypoint": getattr(first[0], "target_node", None),
                                        "distance_to_fix_nm": round(first_remaining, 2),
                                        "eta_to_fix_seconds": round(first_eta_seconds, 1),
                                        "route_index": getattr(first[0], "route_index", None),
                                        "altitude_ft": getattr(first[0], "altitude_ft", None),
                                        "target_altitude_ft": getattr(first[0], "target_altitude_ft", None),
                                        "speed_kts": getattr(first[0], "speed_kts", None),
                                        "target_speed_kts": getattr(first[0], "target_speed_kts", None),
                                        "heading_deg": getattr(first[0], "heading_deg", None),
                                        "predicted_vertical_separation_ft": round(vertical_separation),
                                    },
                                    str(second[0].callsign): {
                                        "route_id": second[1],
                                        "current_waypoint": getattr(second[0], "target_node", None),
                                        "distance_to_fix_nm": round(second_remaining, 2),
                                        "eta_to_fix_seconds": round(second_eta_seconds, 1),
                                        "route_index": getattr(second[0], "route_index", None),
                                        "altitude_ft": getattr(second[0], "altitude_ft", None),
                                        "target_altitude_ft": getattr(second[0], "target_altitude_ft", None),
                                        "speed_kts": getattr(second[0], "speed_kts", None),
                                        "target_speed_kts": getattr(second[0], "target_speed_kts", None),
                                        "heading_deg": getattr(second[0], "heading_deg", None),
                                        "predicted_vertical_separation_ft": round(vertical_separation),
                                    },
                                },
                                "traffic_context": [
                                    {
                                        "callsign": other.callsign,
                                        "position": {"latitude": other.lat, "longitude": other.lon},
                                        "altitude": other.altitude_ft,
                                        "speed": other.speed_kts,
                                        "heading": other.heading_deg,
                                        "route_id": getattr(other, "arrival_route_id", None),
                                        "route": list(getattr(other, "route", [])),
                                        "target_waypoint": getattr(other, "target_node", None),
                                    }
                                    for other in aircraft_list
                                    if other not in (first[0], second[0])
                                    and getattr(other, "phase", "") in ("ARRIVAL", "FINAL")
                                ],
                            })
                            held_this_update.update((id(first[0]), id(second[0])))
                            break

                        if id(first[0]) in held_this_update or id(second[0]) in held_this_update:
                            break

        detected_conflicts.extend(self._detect_approach_merge_conflicts(aircraft_list))

        unique_conflicts = {}
        for conflict in detected_conflicts:
            conflict["session_scope"] = self._decision_session_scope()
            key = mistral_controller.conflict_key(conflict)
            unique_conflicts.setdefault(key, conflict)
        active_holds = {
            aircraft.callsign for aircraft in aircraft_list
            if self.holding_manager.has_active_holding_route(aircraft)
        }
        mistral_controller.clear_conflicts(
            set(unique_conflicts), active_holds,
            session_scope=self._decision_session_scope(),
        )
        mistral_controller.process_pending(
            list(unique_conflicts.values()), self.holding_manager,
            self._record_ai_conflict_decision,
        )
        for conflict in unique_conflicts.values():
            requested = mistral_controller.request_conflict(
                conflict,
                self.holding_manager,
                self._record_ai_conflict_decision,
            )
            if requested:
                first, second = conflict["aircraft"]
                self._record_separation_event(
                    first, second, conflict["waypoint"], "MERGE DETECTED",
                    f"Predicted merge spacing {conflict['predicted_timing'].get('predicted_separation_nm')} NM; "
                    f"minimum {conflict.get('required_separation_nm', self.MIN_MERGE_SEPARATION_NM)} NM",
                )
                timing = conflict.get("aircraft_timing", {})
                print(f"CONFLICT CHECK | fix={conflict['waypoint']} | "
                      f"predicted_separation={conflict['predicted_timing']['predicted_separation_nm']} NM | "
                      f"required={conflict.get('required_separation_nm', self.MIN_MERGE_SEPARATION_NM)} NM | conflict=true")
                for aircraft in conflict["aircraft"]:
                    values = timing.get(str(aircraft.callsign), {})
                    print(f"  callsign={aircraft.callsign} route={values.get('route_id')} "
                          f"current_waypoint={values.get('current_waypoint')} "
                          f"distance_to_fix={values.get('distance_to_fix_nm')} NM "
                          f"ETA_to_fix={values.get('eta_to_fix_seconds')} s")

    def _record_ai_conflict_decision(self, result):
        decision = result["decision"]
        conflict = result["conflict"]
        if not hasattr(self, "ai_decision_log"):
            self.ai_decision_log = []
        selected = decision.get("callsign")
        decision_source = result.get("decision_source", "DETERMINISTIC_FALLBACK")
        self._count_decision_audit("SHARED_WAYPOINT_CONFLICT", decision_source)
        self.ai_decision_log.insert(0, {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "callsign": selected,
            "controller": decision["controller"],
            "decision": decision["decision"],
            "instruction": decision["instruction"],
            "conflict_waypoint": conflict["waypoint"],
            "other_aircraft": next(
                (aircraft.callsign for aircraft in conflict["aircraft"]
                 if aircraft.callsign != selected), None
            ),
            "altitude": decision.get("altitude"),
            "speed": decision.get("speed"),
            "holding_route_id": decision.get("holding_route_id"),
            "hold_circuits": decision.get("hold_circuits"),
            "rejoin_node": decision.get("rejoin_node"),
            "reason": decision["reason"],
            "decision_source": decision_source,
            "lifecycle_state": "COMMITTED" if result.get("holding") else "REJECTED",
            "validation_result": result.get("validation_result"),
            "validation_reason": result.get("validation_reason"),
            "aircraft_ahead": conflict.get("predicted_timing", {}).get("aircraft_ahead"),
            "aircraft_behind": conflict.get("predicted_timing", {}).get("aircraft_behind"),
            "current_separation_nm": conflict.get("predicted_timing", {}).get("current_separation_nm"),
            "relative_closure_rate_kts": conflict.get("predicted_timing", {}).get("relative_closure_rate_kts"),
            "predicted_merge_separation_nm": conflict.get("predicted_timing", {}).get("predicted_separation_nm"),
            "prediction_horizon_seconds": conflict.get("predicted_timing", {}).get("prediction_horizon_seconds"),
            "aircraft_timing": conflict.get("aircraft_timing", {}),
        })
        del self.ai_decision_log[50:]

    def _count_decision_audit(self, decision_type, decision_source):
        if not hasattr(self, "decision_audit_counts"):
            self.decision_audit_counts = {}
        by_type = self.decision_audit_counts.setdefault(str(decision_type), {})
        source = str(decision_source or "UNATTRIBUTED")
        by_type[source] = by_type.get(source, 0) + 1

    def _record_guidance_event(self, aircraft, event, waypoint, reason, decision_source=None):
        if not hasattr(self, "_last_separation_event_at"):
            self._last_separation_event_at = {}
        if not hasattr(self, "ai_decision_log"):
            self.ai_decision_log = []
        event_key = (str(aircraft.callsign), str(event), str(waypoint),
                     getattr(aircraft, "route_index", None),
                     str(reason) if event == "APPROACH CLEARANCE WITHHELD" else None)
        now = datetime.now().astimezone()
        previous = self._last_separation_event_at.get(event_key)
        if previous is not None and (now - previous).total_seconds() < 30:
            return
        self._last_separation_event_at[event_key] = now
        if decision_source is None:
            if str(event).startswith("AI "):
                decision_source = "AI"
            elif str(event).startswith("DETERMINISTIC "):
                decision_source = "DETERMINISTIC_FALLBACK"
            elif event == "APPROACH CLEARANCE WITHHELD":
                decision_source = "SAFETY_REJECTION"
            else:
                decision_source = "UNATTRIBUTED"
        decision_type = ("APPROACH_ENTRY" if "APPROACH ENTRY" in event
                         else "HOLD_RELEASE" if "HOLD RELEASE" in event or "HOLD CONTINUED" in event
                         else "HOLD_LEVEL" if "HOLD LEVEL" in event
                         else "APPROACH_RESOLUTION" if "APPROACH WAIT" in event or "HOLD SELECTED" in event
                         else str(event))
        self._count_decision_audit(decision_type, decision_source)
        logging.getLogger(__name__).info(
            "Aircraft navigation event",
            extra={
                "event": str(event),
                "aircraft_callsign": getattr(aircraft, "callsign", None),
                "route_id": getattr(aircraft, "arrival_route_id", None),
                "route_cursor": getattr(aircraft, "route_index", None),
                "active_route": list(getattr(aircraft, "route", []) or []),
                "assigned_waypoint": getattr(aircraft, "assigned_node", None),
                "target_waypoint": getattr(aircraft, "target_node", None),
                "clearance_direct_node": getattr(getattr(aircraft, "clearance", None), "direct_node", None),
                "distance_nm": getattr(aircraft, "current_distance", None),
                "altitude_ft": getattr(aircraft, "altitude_ft", None),
                "target_altitude_ft": getattr(aircraft, "target_altitude_ft", None),
                "heading_deg": getattr(aircraft, "heading_deg", None),
                "target_heading_deg": getattr(aircraft, "target_heading_deg", None),
                "speed_kts": getattr(aircraft, "speed_kts", None),
                "guidance_mode": getattr(aircraft, "phase", None),
                "hold_active": bool(callable(getattr(getattr(self, "holding_manager", None),
                                                       "has_active_holding_route", None))
                                    and self.holding_manager.has_active_holding_route(aircraft)),
                "approach_clearance": bool(getattr(aircraft, "approach_clearance", False)),
                "approach_entry_node": getattr(aircraft, "_approach_entry_node", None),
                "decision_source": decision_source,
                "reason": reason,
            },
        )
        self.ai_decision_log.insert(0, {
            "timestamp": now.isoformat(timespec="seconds"),
            "callsign": aircraft.callsign,
            "controller": "Arrival",
            "decision": event,
            "instruction": event.replace("_", " "),
            "conflict_waypoint": waypoint,
            "route_id": getattr(aircraft, "arrival_route_id", None),
            "route_index": getattr(aircraft, "route_index", None),
            "active_route": list(getattr(aircraft, "route", []) or []),
            "assigned_waypoint": getattr(aircraft, "assigned_node", None),
            "target_waypoint": getattr(aircraft, "target_node", None),
            "clearance_direct_node": getattr(getattr(aircraft, "clearance", None), "direct_node", None),
            "distance_nm": getattr(aircraft, "current_distance", None),
            "altitude": getattr(aircraft, "altitude_ft", None),
            "target_altitude_ft": getattr(aircraft, "target_altitude_ft", None),
            "speed": getattr(aircraft, "speed_kts", None),
            "target_speed_kts": getattr(aircraft, "target_speed_kts", None),
            "next_constrained_waypoint": getattr(aircraft, "target_node", None),
            "reason": reason,
            "decision_source": decision_source,
        })
        del self.ai_decision_log[50:]

    def _record_separation_event(self, aircraft, other, waypoint, action, reason):
        aircraft_pair = tuple(sorted((str(aircraft.callsign), str(getattr(other, "callsign", "")))))
        event_key = (aircraft_pair, str(waypoint), str(action))
        now = datetime.now().astimezone()
        previous = self._last_separation_event_at.get(event_key)
        if previous is not None and (now - previous).total_seconds() < 30:
            return
        self._last_separation_event_at[event_key] = now
        separation_nm, closure_kts = self._relative_closure(aircraft, other) if other is not None else (None, None)
        ahead, behind = None, None
        eta_ahead, eta_behind = None, None
        if other is not None:
            etas = []
            for item in (aircraft, other):
                route = self.arrival_routes.get(getattr(item, "arrival_route_id", None))
                speed = getattr(item, "target_speed_kts", None) or getattr(item, "speed_kts", 0)
                progress = self._route_progress_nm(item, route) if route else None
                eta = (self._approach_merge_eta_seconds(item, route, progress, float(speed))
                       if route and progress is not None and speed else None)
                etas.append(eta)
            if etas[0] is not None and etas[1] is not None:
                ahead, behind = ((aircraft.callsign, other.callsign) if etas[0] <= etas[1]
                                 else (other.callsign, aircraft.callsign))
                eta_ahead, eta_behind = sorted(etas)
        self.ai_decision_log.insert(0, {
            "timestamp": now.isoformat(timespec="seconds"),
            "callsign": aircraft.callsign,
            "other_aircraft": getattr(other, "callsign", None),
            "controller": "Arrival",
            "decision": action,
            "instruction": f"{action} at shared waypoint {waypoint}",
            "conflict_waypoint": waypoint,
            "altitude": getattr(aircraft, "target_altitude_ft", None),
            "speed": getattr(aircraft, "target_speed_kts", None),
            "current_separation_nm": round(separation_nm, 2) if separation_nm is not None else None,
            "relative_closure_rate_kts": round(closure_kts, 1) if closure_kts is not None else None,
            "aircraft_ahead": ahead,
            "aircraft_behind": behind,
            "eta_ahead_at_merge_seconds": round(eta_ahead, 1) if eta_ahead is not None else None,
            "eta_behind_at_merge_seconds": round(eta_behind, 1) if eta_behind is not None else None,
            "target_speed_kts": getattr(aircraft, "target_speed_kts", None),
            "target_altitude_ft": getattr(aircraft, "target_altitude_ft", None),
            "reason": reason,
        })
        del self.ai_decision_log[50:]

    def _enforce_predictive_separation(self, aircraft_list):
        """Apply speed control for predicted near-term conflicts before movement."""
        traffic = [ac for ac in aircraft_list
                   if getattr(ac, "phase", "") in ("ARRIVAL", "FINAL", "DEPARTURE")]
        kinematics = []
        for aircraft in traffic:
            speed_kts = max(0.0, float(getattr(aircraft, "speed_kts", 0) or 0))
            heading = math.radians(float(getattr(aircraft, "heading_deg", 0) or 0))
            speed_nm_per_second = speed_kts / 3600.0
            kinematics.append((
                aircraft,
                float(aircraft.lat),
                float(aircraft.lon),
                speed_kts,
                speed_nm_per_second * math.sin(heading),
                speed_nm_per_second * math.cos(heading),
            ))

        for first_index, first_data in enumerate(kinematics):
            first, first_lat, first_lon, first_speed_kts, first_east, first_north = first_data
            for second_data in kinematics[first_index + 1:]:
                second, second_lat, second_lon, second_speed_kts, second_east, second_north = second_data
                mean_latitude = math.radians((first_lat + second_lat) / 2.0)
                east = (second_lon - first_lon) * 60.0 * math.cos(mean_latitude)
                north = (second_lat - first_lat) * 60.0
                detection_radius_nm = max(
                    self.MIN_MERGE_SEPARATION_NM,
                    self.CONFLICT_WARNING_DISTANCE_NM,
                    self.CRITICAL_SEPARATION_NM,
                )
                maximum_closure_nm = (
                    detection_radius_nm + (first_speed_kts + second_speed_kts)
                    * self.PREDICTION_LOOKAHEAD_SECONDS / 3600.0 + 1e-9
                )
                if math.hypot(east, north) > maximum_closure_nm:
                    continue
                relative_east = second_east - first_east
                relative_north = second_north - first_north
                relative_speed_sq = relative_east ** 2 + relative_north ** 2
                if relative_speed_sq <= 1e-12:
                    closest_time = 0.0
                else:
                    closest_time = max(0.0, min(self.PREDICTION_LOOKAHEAD_SECONDS,
                        -(east * relative_east + north * relative_north) / relative_speed_sq))
                closest_distance = math.hypot(east + relative_east * closest_time,
                                              north + relative_north * closest_time)
                vertical_difference = abs(
                    (float(getattr(second, "altitude_ft", 0)) - float(getattr(first, "altitude_ft", 0)))
                    + (float(getattr(second, "vertical_speed_fpm", 0))
                       - float(getattr(first, "vertical_speed_fpm", 0))) * closest_time / 60.0
                )
                if closest_distance < self.CONFLICT_WARNING_DISTANCE_NM:
                    self._record_separation_event(
                        first, second, getattr(first, "target_node", None), "SEPARATION WARNING",
                        f"Predicted closest spacing {closest_distance:.2f} NM in {closest_time:.0f} seconds",
                    )
                if closest_distance <= self.CRITICAL_SEPARATION_NM:
                    self._record_separation_event(
                        first, second, getattr(first, "target_node", None), "CRITICAL SEPARATION CONFLICT",
                        f"Aircraft are predicted {closest_distance:.2f} NM apart in {closest_time:.0f} seconds",
                    )
                if closest_distance >= self.MIN_MERGE_SEPARATION_NM:
                    continue

                first_route = self.arrival_routes.get(getattr(first, "arrival_route_id", None), {})
                second_route = self.arrival_routes.get(getattr(second, "arrival_route_id", None), {})
                first_progress = self._route_progress_nm(first, first_route) if first_route else None
                second_progress = self._route_progress_nm(second, second_route) if second_route else None
                if (getattr(first, "arrival_route_id", None) == getattr(second, "arrival_route_id", None)
                        and first_progress is not None and second_progress is not None):
                    yielding, leader = (first, second) if first_progress < second_progress else (second, first)
                else:
                    first_eta = (self._approach_merge_eta_seconds(first, first_route, first_progress, first_speed_kts)
                                 if first_progress is not None else None)
                    second_eta = (self._approach_merge_eta_seconds(second, second_route, second_progress, second_speed_kts)
                                  if second_progress is not None else None)
                    if first_eta is not None and second_eta is not None:
                        yielding, leader = (first, second) if first_eta >= second_eta else (second, first)
                    else:
                        yielding, leader = (first, second) if first_speed_kts <= second_speed_kts else (second, first)

                target = getattr(yielding, "target_speed_kts", None)
                leader_speed = float(getattr(leader, "speed_kts", 0) or 0)
                minimum_speed = self._minimum_sequencing_speed(yielding)
                if isinstance(target, (int, float)) and target > minimum_speed:
                    deficit = self.MIN_MERGE_SEPARATION_NM - closest_distance
                    adjusted = max(minimum_speed, float(target) - self.MAX_SEQUENCING_SPEED_DELTA_KTS,
                                   min(float(target), leader_speed - max(0.25, deficit) * 12.0))
                    if adjusted < target:
                        if not hasattr(yielding, "_engine_predictive_original_target_speed"):
                            yielding._engine_predictive_original_target_speed = target
                        yielding.assign_speed(adjusted)
                        yielding._sequence_speed_ceiling = adjusted
                        yielding._engine_predictive_applied_target_speed = adjusted
                        self._record_separation_event(
                            yielding, leader, getattr(yielding, "target_node", None), "SPEED REDUCTION",
                            f"Predicted closest spacing {closest_distance:.2f} NM in {closest_time:.0f} seconds",
                        )
                        continue

                if closest_distance < self.MIN_MERGE_SEPARATION_NM:
                    merge = self._next_common_route_waypoint(yielding, leader)
                    options = self.holding_manager.compatible_holding_options(yielding, merge) if merge else []
                    held = False
                    required_hold_delay = (
                        closest_time + max(0.0, self.MIN_MERGE_SEPARATION_NM - closest_distance)
                        / math.sqrt(relative_speed_sq)
                        if relative_speed_sq > 1e-12 else None
                    )
                    for option in options:
                        result = self.holding_manager.apply_holding_route(
                            yielding, option["holding_route_id"], circuits=1,
                            required_delay_seconds=required_hold_delay,
                        )
                        if result.get("success"):
                            held = True
                            self._record_separation_event(
                                yielding, leader, merge, "HOLD",
                                "Critical predicted separation; aircraft entered its configured hold",
                            )
                            break
                    if not held and not self.holding_manager.has_active_holding_route(yielding):
                        candidates = [float(getattr(leader, "altitude_ft", 0)) + 1000.0,
                                      float(getattr(leader, "altitude_ft", 0)) - 1000.0]
                        candidates = [level for level in candidates if level >= 1000.0 and all(
                            other is yielding or other is leader
                            or abs(float(getattr(other, "altitude_ft", 0)) - level) >= self.MIN_VERTICAL_SEPARATION_FT
                            for other in traffic
                        )]
                        if candidates:
                            selected_altitude = min(candidates, key=lambda level: abs(level - yielding.altitude_ft))
                            if not hasattr(yielding, "_separation_original_target_altitude"):
                                yielding._separation_original_target_altitude = yielding.target_altitude_ft
                            yielding.assign_altitude(selected_altitude)
                            yielding._separation_applied_target_altitude = selected_altitude
                            yielding.separation_status = "CRITICAL CONFLICT"
                            self._record_separation_event(
                                yielding, leader, merge or getattr(yielding, "target_node", None),
                                "ALTITUDE SEPARATION",
                                f"Emergency vertical resolution assigned {selected_altitude:.0f} ft",
                            )

    @staticmethod
    def _next_common_route_waypoint(first, second):
        first_route = getattr(first, "route", [])
        second_route = getattr(second, "route", [])
        first_index = max(0, int(getattr(first, "route_index", 0)))
        second_index = max(0, int(getattr(second, "route_index", 0)))
        second_names = {str(name).upper() for name in second_route[second_index:]}
        return next((name for name in first_route[first_index:]
                     if str(name).upper() in second_names), None)

    def _record_route_following_actions(self, aircraft_list):
        by_route = {}
        for aircraft in aircraft_list:
            if not hasattr(aircraft, "_separation_target_speed"):
                continue
            route = self.arrival_routes.get(getattr(aircraft, "arrival_route_id", None))
            if not route:
                continue
            progress = self._route_progress_nm(aircraft, route)
            if progress is not None:
                by_route.setdefault(aircraft.arrival_route_id, []).append((progress, aircraft))
        for arrivals in by_route.values():
            arrivals.sort(key=lambda item: item[0])
            for index, (_, follower) in enumerate(arrivals):
                if not hasattr(follower, "_separation_target_speed"):
                    continue
                leader = next((ac for _, ac in arrivals[index + 1:]), None)
                self._record_separation_event(
                    follower, leader, getattr(follower, "target_node", None), "SPEED REDUCTION",
                    f"Same-route following spacing applied at {getattr(follower, 'target_speed_kts', 0):.0f} knots",
                )

    def _choose_holding_level(self, aircraft, hold):
        current_level = hold.get("target_holding_level_ft")
        holding_id = hold.get("holding_route_id")
        safe_levels = self.holding_manager.safe_holding_levels(aircraft, holding_id)
        interval = int(hold.get("completed_circuits", 0)) // 2
        decision_id = ("holding-level", *self._decision_session_scope(),
                       str(aircraft.callsign), str(holding_id), interval)
        occupants = [
            {"callsign": item.get("callsign"), "level_ft": item.get("holding_level_ft"),
             "circuits_completed": item.get("circuits_completed")}
            for item in self.holding_manager.active_holding_routes()
            if str(item.get("holding_route_id")) == str(holding_id)
        ]
        snapshot = {
            "session_scope": self._decision_session_scope(),
            "callsign": str(aircraft.callsign), "holding_route_id": str(holding_id),
            "current_level_ft": float(current_level or aircraft.altitude_ft),
            "safe_levels_ft": tuple(int(level) for level in safe_levels),
            "occupants": tuple(sorted((str(item["callsign"]), item["level_ft"])
                                       for item in occupants)),
        }
        holding_config = self.holding_manager._holding_route_by_id(holding_id) or {}
        fallback_level = self.holding_manager._select_holding_level(
            aircraft, holding_config,
            self.holding_manager._flight_level(holding_config.get("lower_limit")),
            self.holding_manager._flight_level(holding_config.get("upper_limit")),
            exclude_aircraft=aircraft, exclude_level=current_level,
        ) if safe_levels else None
        fallback = str(int(fallback_level)) if fallback_level is not None else "REMAIN"
        choices = {"REMAIN", *(str(int(level)) for level in safe_levels)}
        prompt = json.dumps({
            "task": "After a completed circuit, choose REMAIN or one safe holding level listed below.",
            "aircraft": {"callsign": aircraft.callsign,
                         "type": getattr(aircraft, "aircraft_type", None),
                         "altitude_ft": aircraft.altitude_ft,
                         "current_holding_level_ft": current_level,
                         "speed_kts": aircraft.speed_kts,
                         "holding_fix": hold.get("rejoin_waypoint"),
                         "circuits_completed": hold.get("completed_circuits"),
                         "holding_route_id": holding_id},
            "available_safe_levels_ft": safe_levels,
            "other_aircraft_in_hold": occupants,
            "hold_capacity": holding_config.get("capacity"),
            "policy": "Level changes are considered only at the existing eligible circuit boundary; use 2,000 ft spacing when practical.",
            "choice_schema": {"choice": "REMAIN or one listed level in feet", "reason": "short rationale"},
        }, separators=(",", ":"))
        result = (mistral_controller.operational_choice(
            decision_id, prompt, choices, snapshot, fallback
        ) if getattr(self, "ai_arrival_decisions_enabled", False) else {
            "status": "decided", "choice": fallback, "source": "deterministic_fallback",
            "reason": "AI path disabled for this engine instance",
        })
        if result["status"] == "pending":
            return {"choice": "PENDING", "level_ft": None, "source": "pending"}
        if result["choice"] == "REMAIN":
            self._record_guidance_event(
                aircraft, "AI HOLD LEVEL RETAINED", hold.get("rejoin_waypoint"),
                f"{result.get('reason', '')}; source={result.get('source')}",
            )
            return {"choice": "REMAIN", "level_ft": None, "source": result.get("source")}
        try:
            selected_level = float(result["choice"])
        except (TypeError, ValueError):
            selected_level = None
        if selected_level not in safe_levels:
            selected_level = fallback_level
            source = "deterministic_fallback"
            reason = "AI level no longer passes current holding-level safety validation"
        else:
            source = result.get("source")
            reason = result.get("reason", "")
        if selected_level is None:
            return {"choice": "REMAIN", "level_ft": None, "source": source}
        self._record_guidance_event(
            aircraft, "AI HOLD LEVEL SELECTED" if source == "Mistral"
            else "DETERMINISTIC HOLD LEVEL SELECTED",
            hold.get("rejoin_waypoint"),
            f"{current_level} ft to {selected_level:g} ft: {reason}; source={source}",
        )
        return {"choice": "CHANGE", "level_ft": selected_level, "source": source}

    def _can_release_holding(self, aircraft):
        hold = self.holding_manager.state_for(aircraft)
        if hold is None:
            return self._holding_release_denial(
                aircraft, None, "Active holding state is missing",
                failed_condition="active_holding_state_missing",
            )
        holding_config = next((item for item in getattr(self, "holding_routes", [])
                               if isinstance(item, dict)
                               and str(item.get("id")) == str(hold.get("holding_route_id"))), {})
        area = str(hold.get("rejoin_waypoint", "")).upper()
        area_capacity = holding_config.get("downstream_capacity")
        if area_capacity is not None:
            try:
                area_capacity = max(1, int(area_capacity))
            except (TypeError, ValueError):
                area_capacity = 6
            occupancy = self._downstream_segment_occupancy(aircraft, area)
            if occupancy >= area_capacity:
                return self._holding_release_denial(
                    aircraft, hold,
                    f"Downstream segment at {area} is at capacity ({occupancy}/{area_capacity})",
                    failed_condition="downstream_segment_capacity_reached",
                    conflict_waypoint=area, downstream_occupancy=occupancy,
                    downstream_capacity=area_capacity,
                )
        priority_callsign = hold.get("priority_callsign")
        protected_merge_point = hold.get("protected_merge_point")
        if priority_callsign and protected_merge_point:
            priority = next((item for item in self.manager.all()
                             if str(item.callsign) == str(priority_callsign)), None)
            priority_cleared = priority is None
            priority_route_id = getattr(priority, "arrival_route_id", None) if priority is not None else None
            priority_index = getattr(priority, "route_index", None) if priority is not None else None
            merge_index = None
            if priority is not None:
                priority_route = self.arrival_routes.get(priority_route_id, {})
                priority_points = priority_route.get("waypoints", []) if isinstance(priority_route, dict) else []
                merge_index = next((index for index, point in enumerate(priority_points)
                                    if str(point.get("name", "")).upper()
                                    == str(protected_merge_point).upper()), None)
                priority_cleared = (
                    merge_index is not None and isinstance(priority_index, int)
                    and priority_index > merge_index
                    and not self.holding_manager.has_active_holding_route(priority)
                )
            if not priority_cleared:
                return self._holding_release_denial(
                    aircraft, hold,
                    f"Waiting for priority aircraft {priority_callsign} to clear {protected_merge_point}",
                    failed_condition="priority_aircraft_not_clear_of_protected_merge",
                    other_aircraft=priority_callsign,
                    conflict_waypoint=protected_merge_point,
                    priority_route_id=priority_route_id,
                    priority_route_index=priority_index,
                    priority_merge_index=merge_index,
                    priority_is_holding=(self.holding_manager.has_active_holding_route(priority)
                                         if priority is not None else None),
                )
            # Clearing the protected merge point is necessary but not
            # sufficient: continue through approach-entry and reservation
            # validation before authorizing hold release.
        route_id = getattr(aircraft, "arrival_route_id", None)
        route_definition = self.arrival_routes.get(route_id, {})
        route_points = route_definition.get("waypoints", [])
        route_names = hold["original_route"]
        fix_name = str(hold["rejoin_waypoint"]).upper()
        fix_index = next((i for i, name in enumerate(route_names) if str(name).upper() == fix_name), None)
        if fix_index is None:
            return self._holding_release_denial(
                aircraft, hold, "Holding fix is not on the permanent route",
                failed_condition="holding_fix_not_on_saved_route", holding_fix=fix_name,
            )

        def cumulative(points):
            values = [0.0]
            for previous, current in zip(points, points[1:]):
                values.append(values[-1] + self._route_segment_length_nm(
                    {"latitude": previous["latitude"], "longitude": previous["longitude"]},
                    {"latitude": current["latitude"], "longitude": current["longitude"]},
                ))
            return values

        by_name = {str(point.get("name", "")).upper(): point for point in route_points if isinstance(point, dict)}
        try:
            held_points = [by_name[str(name).upper()] for name in route_names]
            held_progress = cumulative(held_points)
        except (KeyError, TypeError, ValueError):
            return self._holding_release_denial(
                aircraft, hold, "Permanent route coordinates are incomplete",
                failed_condition="permanent_route_coordinates_incomplete",
                missing_route_waypoints=[name for name in route_names
                                         if str(name).upper() not in by_name],
            )
        own_speed = getattr(aircraft, "target_speed_kts", None) or getattr(aircraft, "speed_kts", 0)
        own_altitude = getattr(aircraft, "altitude_ft", 0)
        if not isinstance(own_speed, (int, float)) or own_speed <= 0:
            return self._holding_release_denial(
                aircraft, hold, "No valid speed for rejoin prediction",
                failed_condition="invalid_rejoin_speed", target_speed_kts=own_speed,
            )

        downstream = []
        for index in range(fix_index + 1, len(route_names)):
            downstream.append((route_names[index], held_points[index],
                               (held_progress[index] - held_progress[fix_index]) * 3600.0 / own_speed))
        traffic = self.manager.all()
        for other in traffic:
            if other is aircraft or getattr(other, "phase", "") not in {"ARRIVAL", "FINAL"}:
                continue
            other_route_id = getattr(other, "arrival_route_id", None)
            other_definition = self.arrival_routes.get(other_route_id)
            if not isinstance(other_definition, dict):
                continue
            other_state = self.holding_manager.state_for(other)
            if (other_state is not None
                    and other_state.get("holding_route_id") == hold.get("holding_route_id")):
                continue
            other_route_points = other_definition.get("waypoints", [])
            try:
                other_progress = self._route_progress_nm(other, other_definition)
            except (AttributeError, KeyError, TypeError, ValueError):
                other_progress = None
            if other_progress is None:
                continue
            other_speed = getattr(other, "target_speed_kts", None) or getattr(other, "speed_kts", 0)
            if not isinstance(other_speed, (int, float)) or other_speed <= 0:
                continue
            try:
                other_cumulative = cumulative(other_route_points)
            except (KeyError, TypeError, ValueError):
                return self._holding_release_denial(
                    aircraft, hold, f"{other.callsign} route coordinates are unavailable",
                    failed_condition="blocking_aircraft_route_coordinates_unavailable",
                    other_aircraft=other.callsign,
                )
            for name, held_point, held_eta in downstream:
                for other_index, other_point in enumerate(other_route_points):
                    same_name = str(other_point.get("name", "")).upper() == str(name).upper()
                    try:
                        same_position = self._route_segment_length_nm(held_point, other_point) <= 0.05
                    except (KeyError, TypeError, ValueError):
                        same_position = False
                    if not (same_name or same_position) or other_cumulative[other_index] < other_progress - 0.1:
                        continue
                    if other_state is not None:
                        if other_state["completed_circuits"] < other_state["requested_circuits"]:
                            continue
                        own_priority = HoldingManager.holding_priority_score(hold)
                        other_priority = HoldingManager.holding_priority_score(other_state)
                        if own_priority >= other_priority:
                            continue
                        return self._holding_release_denial(
                            aircraft, hold,
                            f"{other.callsign} has higher circuit-and-age holding priority and is ready to rejoin the downstream route",
                            failed_condition="higher_priority_holding_aircraft_ready_to_rejoin",
                            other_aircraft=other.callsign, conflict_waypoint=name,
                        )
                    time_gap = abs(held_eta - (other_cumulative[other_index] - other_progress) * 3600.0 / other_speed)
                    other_altitude = getattr(other, "altitude_ft", 0)
                    predicted_gap_nm = time_gap * max(float(own_speed), float(other_speed)) / 3600.0
                    if predicted_gap_nm < self.MIN_MERGE_SEPARATION_NM:
                        return self._holding_release_denial(
                            aircraft, hold, f"{other.callsign} is predicted to merge at {name}",
                            failed_condition="predicted_downstream_merge_spacing",
                            other_aircraft=other.callsign, conflict_waypoint=name,
                            predicted_separation_nm=round(predicted_gap_nm, 2),
                            required_separation_nm=self.MIN_MERGE_SEPARATION_NM,
                        )

        own_eta = self._approach_merge_eta_seconds(
            aircraft, route_definition, held_progress[fix_index], float(own_speed)
        )
        if own_eta is not None:
            for other in traffic:
                if other is aircraft or getattr(other, "phase", "") not in {"ARRIVAL", "FINAL"}:
                    continue
                other_state = self.holding_manager.state_for(other)
                if (other_state is not None
                        and other_state.get("holding_route_id") == hold.get("holding_route_id")):
                    continue
                other_route = self.arrival_routes.get(getattr(other, "arrival_route_id", None))
                other_speed = getattr(other, "target_speed_kts", None) or getattr(other, "speed_kts", 0)
                if not isinstance(other_route, dict) or not isinstance(other_speed, (int, float)) or other_speed <= 0:
                    continue
                other_progress = self._route_progress_nm(other, other_route)
                if other_progress is None:
                    continue
                other_eta = self._approach_merge_eta_seconds(
                    other, other_route, other_progress, float(other_speed)
                )
                if other_eta is None or abs(own_eta - other_eta) * max(float(own_speed), float(other_speed)) / 3600.0 >= self.MIN_MERGE_SEPARATION_NM:
                    continue
                final_approach_fix = (self.approach_ai.approach_points[-1].get("id")
                                      if self.approach_ai.approach_points else "downstream approach")
                return self._holding_release_denial(
                    aircraft, hold,
                    f"{other.callsign} is predicted to converge on the downstream approach at {final_approach_fix}",
                    failed_condition="predicted_downstream_approach_spacing",
                    other_aircraft=other.callsign, conflict_waypoint=final_approach_fix,
                    predicted_separation_nm=round(
                        abs(own_eta - other_eta) * max(float(own_speed), float(other_speed)) / 3600.0, 2
                    ),
                    required_separation_nm=self.MIN_MERGE_SEPARATION_NM,
                )
        handoff = self._can_approach_handoff(aircraft)
        if not handoff.get("safe"):
            return self._holding_release_denial(
                aircraft, hold,
                handoff.get("reason", "Configured approach transition is unavailable"),
                failed_condition="approach_handoff_rejected",
                handoff_details={key: value for key, value in handoff.items() if key != "safe"},
            )
        # The deterministic validator owns the release decision. An AI pending
        # response or preference to continue cannot veto an already safe release.
        return {"safe": True, "reason": "Downstream route clear and required separation available",
                "decision_source": "deterministic_safety_validation",
                "approach_entry_index": handoff["approach_entry_index"],
                "approach_entry_node": handoff["approach_entry_node"]}
        decision_id = ("hold-release", *self._decision_session_scope(), str(aircraft.callsign),
                       str(hold.get("holding_route_id")), str(route_id))
        reservations = tuple(sorted(
            (str(node), str(getattr(owner, "callsign", "")))
            for node, owner in getattr(self, "approach_reservations", {}).items()
        ))
        snapshot = {
            "session_scope": self._decision_session_scope(),
            "callsign": str(aircraft.callsign),
            "arrival_route_id": str(route_id),
            "holding_route_id": str(hold.get("holding_route_id")),
            "holding_level_ft": float(hold.get("target_holding_level_ft") or aircraft.altitude_ft),
            "rejoin_waypoint": str(hold.get("rejoin_waypoint", "")),
            "completed_circuits": int(hold.get("completed_circuits", 0)),
            "route_index": getattr(aircraft, "route_index", None),
            "assigned_node": str(getattr(aircraft, "assigned_node", "")),
            "target_node": str(getattr(aircraft, "target_node", "")),
            "position": (round(float(getattr(aircraft, "lat", 0)), 5),
                         round(float(getattr(aircraft, "lon", 0)), 5)),
            "approach_entry_node": handoff["approach_entry_node"],
            "feasible_approach_entries": tuple(
                item["node"] for item in handoff.get("approach_candidates_considered", [])
                if item.get("eligible")
            ),
            "approach_reservations": reservations,
        }
        nearby = [{
            "callsign": item.callsign, "type": getattr(item, "aircraft_type", None),
            "position": [item.lat, item.lon], "altitude_ft": item.altitude_ft,
            "speed_kts": item.speed_kts, "route_id": getattr(item, "arrival_route_id", None),
            "next_waypoint": getattr(item, "target_node", None),
            "holding": (self.holding_manager.holding_status(item)
                        if callable(getattr(self.holding_manager, "holding_status", None)) else None),
        } for item in traffic if getattr(item, "phase", "") in {"ARRIVAL", "FINAL"}]
        prompt = json.dumps({
            "task": "Choose RELEASE only if this held aircraft should rejoin now; otherwise choose CONTINUE.",
            "aircraft": {"callsign": aircraft.callsign, "type": getattr(aircraft, "aircraft_type", None),
                         "position": [aircraft.lat, aircraft.lon], "altitude_ft": aircraft.altitude_ft,
                         "holding_level_ft": snapshot["holding_level_ft"],
                         "speed_kts": aircraft.speed_kts, "route": hold.get("original_route", []),
                         "rejoin_waypoint": hold.get("rejoin_waypoint"),
                         "rejoin_node": hold.get("rejoin_node"),
                         "circuits_completed": hold.get("completed_circuits"),
                         "target_waypoint": getattr(aircraft, "target_node", None)},
            "validated_safe_release": {"approach_entry_node": handoff["approach_entry_node"],
                                       "approach_entry_index": handoff["approach_entry_index"],
                                       "reason": handoff.get("reason")},
            "currently_feasible_approach_entries": handoff.get("approach_candidates_considered", []),
            "holding_capacity": {"route_id": hold.get("holding_route_id"),
                                 "occupancy": (self.holding_manager.holding_occupancy(hold.get("holding_route_id"))
                                               if callable(getattr(self.holding_manager, "holding_occupancy", None)) else None),
                                 "capacity": holding_config.get("capacity"),
                                 "assigned_level_ft": snapshot["holding_level_ft"]},
            "entry_reservations": [
                {"node": node, "callsign": owner.callsign,
                 "status": getattr(self, "approach_reservation_status", {}).get(node, "active")}
                for node, owner in getattr(self, "approach_reservations", {}).items()
            ],
            "nearby_arrivals": nearby,
            "choice_schema": {"choice": "RELEASE or CONTINUE", "reason": "short rationale"},
        }, separators=(",", ":"))
        choice = mistral_controller.operational_choice(
            decision_id, prompt, {"RELEASE", "CONTINUE"}, snapshot, "RELEASE"
        )
        if choice["status"] == "pending":
            return self._holding_release_denial(
                aircraft, hold, "Waiting for AI hold-release decision",
                failed_condition="ai_release_decision_pending",
                conflict_waypoint=handoff["approach_entry_node"],
            )
        if choice["choice"] == "CONTINUE":
            mistral_controller.clear_operational_decision(decision_id)
            self._release_approach_reservation(aircraft)
            self._record_guidance_event(
                aircraft, "AI HOLD CONTINUED", hold.get("rejoin_waypoint"),
                choice.get("reason", "AI selected another holding circuit"),
            )
            return self._holding_release_denial(
                aircraft, hold, choice.get("reason", "AI selected another holding circuit"),
                failed_condition="ai_selected_continue_holding",
            )
        self._record_guidance_event(
            aircraft, "AI HOLD RELEASE SELECTED", handoff["approach_entry_node"],
            f"{choice.get('reason', '')}; source={choice.get('source')}",
        )
        return {"safe": True, "reason": choice.get("reason", "AI selected safe hold release"),
                "decision_source": choice.get("source"),
                "approach_entry_index": handoff["approach_entry_index"],
                "approach_entry_node": handoff["approach_entry_node"]}

    def _downstream_segment_occupancy(self, releasing_aircraft, area):
        """Count aircraft actually past this area's hold fix on compatible routes."""
        count = 0
        for other in self.manager.all():
            if (other is releasing_aircraft
                    or getattr(other, "phase", "") not in {"ARRIVAL", "FINAL"}
                    or self.holding_manager.has_active_holding_route(other)):
                continue
            route_id = str(getattr(other, "arrival_route_id", ""))
            route = self.arrival_routes.get(route_id)
            if not isinstance(route, dict):
                continue
            names = [str(point.get("name", "")).upper()
                     for point in route.get("waypoints", []) if isinstance(point, dict)]
            if area not in names:
                continue
            cursor = getattr(other, "route_index", None)
            if not isinstance(cursor, int) or isinstance(cursor, bool):
                continue
            # route_index is the next active target for arrival navigation.
            # Count only after the area fix and through this route's approach
            # trigger; aircraft still upstream are not occupying the segment.
            area_index = names.index(area)
            trigger = str(route.get("approach_trigger", "")).upper()
            trigger_index = next((i for i in range(area_index + 1, len(names))
                                  if names[i] == trigger), len(names) - 1)
            if area_index < cursor <= trigger_index or (
                    getattr(other, "phase", "") == "FINAL" and cursor > area_index):
                count += 1
        return count

    def _holding_release_denial(self, aircraft, hold, reason, **details):
        """Log a throttled structured explanation while preserving the denial result."""
        callsign = str(getattr(aircraft, "callsign", "<unknown>"))
        hold_id = (hold.get("holding_route_id") if isinstance(hold, dict) else
                   getattr(aircraft, "holding_route_id", None))
        completed = hold.get("completed_circuits") if isinstance(hold, dict) else None
        requested = hold.get("requested_circuits") if isinstance(hold, dict) else None
        key = (callsign, str(hold_id), str(reason))
        now = time.monotonic()
        last_emitted = getattr(self, "_last_holding_release_diagnostic_at", {})
        previous = last_emitted.get(key)
        if previous is None or now - previous >= 30.0:
            last_emitted[key] = now
            self._last_holding_release_diagnostic_at = last_emitted
            logging.getLogger(__name__).warning(
                "Holding release denied: %s", reason,
                extra={
                    "event": "holding_release_denied",
                    "aircraft_callsign": callsign,
                    "hold_id": hold_id,
                    "circuits_completed": completed,
                    "circuits_requested": requested,
                    "release_denial_reason": reason,
                    "blocking_aircraft": details.get("other_aircraft"),
                    "blocking_condition": details.get("failed_condition"),
                    "blocking_waypoint": details.get("conflict_waypoint"),
                    "release_details": details,
                },
            )
        result_keys = {"other_aircraft", "conflict_waypoint", "predicted_separation_nm"}
        failed_condition = details.get("failed_condition")
        result = {"safe": False, "reason": reason,
                  **{key: value for key, value in details.items() if key in result_keys}}
        if failed_condition == "ai_selected_continue_holding":
            result["decision_source"] = "AI"
        elif failed_condition == "ai_release_decision_pending":
            result["decision_source"] = "AI_PENDING"
        return result

    def _can_approach_handoff(self, aircraft, from_fallback=False):
        with self._approach_allocation_lock:
            return self._evaluate_approach_handoff(aircraft, from_fallback=from_fallback)

    def _has_configured_holding_continuation(self, aircraft, trigger):
        route = self.arrival_routes.get(getattr(aircraft, "arrival_route_id", None))
        if not isinstance(route, dict):
            return False
        points = route.get("waypoints", [])
        trigger = str(trigger).upper()
        if (not points or str(route.get("approach_trigger", "")).upper() != trigger
                or str(points[-1].get("name", "")).upper() != trigger):
            return False
        endpoint = points[-1]
        return any(
            isinstance(point.get("arrival_entry_transition"), dict)
            and point["arrival_entry_transition"].get("type") == "DIRECT_FROM_ARRIVAL_TRIGGER"
            and self._route_segment_length_nm(
                endpoint, {"latitude": point["latitude"], "longitude": point["longitude"]}
            ) <= self.SEQUENCING_WINDOW_NM
            for point in self.approach_ai.approach_points
        )

    def _evaluate_approach_handoff(self, aircraft, from_fallback=False):
        if not hasattr(self, "approach_reservation_status"):
            self.approach_reservation_status = {}
        self._prune_approach_reservations()
        route = self.arrival_routes.get(getattr(aircraft, "arrival_route_id", None))
        if not isinstance(route, dict):
            self._release_approach_reservation(aircraft)
            return {"safe": False, "reason": "Arrival route is unavailable for approach sequencing"}
        own_speed = getattr(aircraft, "target_speed_kts", None) or getattr(aircraft, "speed_kts", 0)
        own_progress = self._route_progress_nm(aircraft, route)
        if own_progress is None or own_speed <= 0:
            self._release_approach_reservation(aircraft)
            return {"safe": False, "reason": "Approach trajectory cannot be predicted"}
        traffic = self.manager.all()

        # Consider each configured approach point, then validate its explicit
        # transition and operational feasibility below. This keeps an absent
        # or unsupported transition visible in diagnostics instead of silently
        # dropping that node from the candidate set.
        points = self.approach_ai.approach_points
        entries = [(index, point) for index, point in enumerate(points)
                   if isinstance(point, dict) and point.get("id")]
        if not entries:
            self._release_approach_reservation(aircraft)
            return {"safe": False, "reason": "No configured arrival-trigger approach transition is available"}

        prior_entry = next((node for node, owner in self.approach_reservations.items()
                            if owner is aircraft), None)
        configured_preferences = route.get("approach_entry_preferences", [])
        if not isinstance(configured_preferences, list):
            configured_preferences = []
        preference_rank = {
            str(node).strip().upper(): rank
            for rank, node in enumerate(configured_preferences)
            if isinstance(node, str) and node.strip()
        }
        candidates = []
        failures = []
        candidate_diagnostics = []
        required_hold_delays = []
        for index, point in entries:
            node = str(point["id"]).upper()
            transition = point.get("arrival_entry_transition")
            if (not isinstance(transition, dict)
                    or transition.get("type") != "DIRECT_FROM_ARRIVAL_TRIGGER"):
                reason = f"{node} has no supported transition from the arrival trigger"
                failures.append(reason)
                candidate_diagnostics.append({"node": node, "eligible": False, "reason": reason})
                continue
            owner = self.approach_reservations.get(node)
            if owner is not None and owner is not aircraft:
                reason = f"{node} reserved for {owner.callsign}"
                failures.append(reason)
                candidate_diagnostics.append({"node": node, "eligible": False, "reason": reason})
                continue
            eta = self._approach_entry_eta_seconds(aircraft, route, own_progress,
                                                   float(own_speed), point,
                                                   from_fallback=from_fallback)
            if eta is None:
                reason = f"{node} trajectory cannot be predicted"
                failures.append(reason)
                candidate_diagnostics.append({"node": node, "eligible": False, "reason": reason})
                continue
            speed = float(own_speed)
            predicted_altitude = float(aircraft.altitude_ft) + float(
                getattr(aircraft, "vertical_speed_fpm", 0) or 0
            ) * eta / 60.0
            target_altitude = float(point.get("altitude_ft", predicted_altitude))
            required_vs = (target_altitude - float(aircraft.altitude_ft)) * 60.0 / max(eta, 1.0)
            if abs(required_vs) > 1500.0:
                reason = f"{node} altitude is not reachable at current readiness"
                failures.append(reason)
                candidate_diagnostics.append({"node": node, "eligible": False, "reason": reason})
                continue

            # Routes must end close enough to join the selected entry through
            # the configured approach geometry.
            route_points = route.get("waypoints", [])
            trigger = str(route.get("approach_trigger", "")).upper()
            if (not route_points
                    or str(route_points[-1].get("name", "")).upper() != trigger):
                reason = f"{node} direct transition does not start at the configured arrival trigger"
                failures.append(reason)
                candidate_diagnostics.append({"node": node, "eligible": False, "reason": reason})
                continue
            endpoint = route_points[-1]
            connector_nm = self._route_segment_length_nm(
                endpoint, {"latitude": point["latitude"], "longitude": point["longitude"]}
            )
            if connector_nm > self.SEQUENCING_WINDOW_NM:
                reason = f"{node} is incompatible with this arrival route"
                failures.append(reason)
                candidate_diagnostics.append({"node": node, "eligible": False, "reason": reason})
                continue

            occupied = None
            for other in traffic:
                if (other is aircraft
                        or getattr(other, "phase", "") not in {"ARRIVAL", "FINAL"}
                        or self._route_segment_length_nm(
                            {"latitude": point["latitude"], "longitude": point["longitude"]},
                            {"latitude": other.lat, "longitude": other.lon},
                        ) >= self.MIN_MERGE_SEPARATION_NM
                        ):
                    continue
                # A nearby aircraft is not an automatic entry conflict if its
                # validated route and ETA show it will clear before this
                # aircraft arrives. The downstream separation checks below
                # remain authoritative. Keep the conservative occupancy
                # rejection only when that traffic cannot be predicted.
                other_route = self.arrival_routes.get(getattr(other, "arrival_route_id", None))
                other_speed = getattr(other, "target_speed_kts", None) or getattr(other, "speed_kts", 0)
                other_progress = (self._route_progress_nm(other, other_route)
                                  if isinstance(other_route, dict)
                                  and isinstance(other_speed, (int, float)) and other_speed > 0
                                  and not self.holding_manager.has_active_holding_route(other)
                                  else None)
                other_etas = (self._approach_point_etas(
                    other, other_route, other_progress, float(other_speed)
                ) if other_progress is not None else {})
                if not set(range(index, len(points))).intersection(other_etas):
                    occupied = other
                    break
            if occupied is not None:
                reason = f"{node} is currently occupied by {occupied.callsign} and downstream ETA cannot be predicted"
                failures.append(reason)
                candidate_diagnostics.append({"node": node, "eligible": False, "reason": reason})
                continue

            minimum_margin = float("inf")
            unsafe_reason = None
            speed_solution = None
            for other in traffic:
                if other is aircraft or getattr(other, "phase", "") not in {"ARRIVAL", "FINAL"}:
                    continue
                if self.holding_manager.has_active_holding_route(other):
                    continue
                other_route = self.arrival_routes.get(getattr(other, "arrival_route_id", None))
                other_speed = getattr(other, "target_speed_kts", None) or getattr(other, "speed_kts", 0)
                if not isinstance(other_route, dict) or not isinstance(other_speed, (int, float)) or other_speed <= 0:
                    continue
                other_progress = self._route_progress_nm(other, other_route)
                if other_progress is None:
                    continue
                other_etas = self._approach_point_etas(other, other_route, other_progress, float(other_speed))
                for downstream_index in set(range(index, len(points))).intersection(other_etas):
                    own_eta = eta + sum(
                        self._route_segment_length_nm(points[j - 1], points[j]) * 3600.0 / speed
                        for j in range(index + 1, downstream_index + 1)
                    )
                    other_eta = other_etas[downstream_index]
                    own_alt = target_altitude + float(
                        getattr(aircraft, "vertical_speed_fpm", 0) or 0
                    ) * (own_eta - eta) / 60.0
                    other_alt = float(other.altitude_ft) + float(
                        getattr(other, "vertical_speed_fpm", 0) or 0
                    ) * other_eta / 60.0
                    gap = abs(own_eta - other_eta) * max(speed, float(other_speed)) / 3600.0
                    minimum_margin = min(minimum_margin, gap - self.MIN_MERGE_SEPARATION_NM)
                    if gap < self.MIN_MERGE_SEPARATION_NM:
                        required_hold_delays.append(max(
                            0.0,
                            other_eta + self.MIN_MERGE_SEPARATION_NM * 3600.0
                            / max(speed, float(other_speed)) - own_eta,
                        ))
                        if own_eta > other_eta:
                            desired_eta = other_eta + self.MIN_MERGE_SEPARATION_NM * 3600.0 / max(
                                speed, float(other_speed)
                            )
                            required_speed = own_eta * speed / max(desired_eta, 1.0)
                            minimum_speed = self._minimum_sequencing_speed(aircraft)
                            minimum_sequencing_target = max(
                                minimum_speed, speed - self.MAX_SEQUENCING_SPEED_DELTA_KTS
                            )
                            if minimum_sequencing_target <= required_speed < speed - 1.0:
                                speed_solution = (required_speed if speed_solution is None
                                                  else min(speed_solution, required_speed))
                                continue
                        unsafe_reason = (
                            f"{other.callsign} is predicted inside {self.MIN_MERGE_SEPARATION_NM:g} NM at "
                            f"{points[downstream_index].get('id', 'approach fix')}"
                        )
                        break
                if unsafe_reason:
                    break
            if unsafe_reason:
                reason = f"{node}: {unsafe_reason}"
                failures.append(reason)
                candidate_diagnostics.append({"node": node, "eligible": False, "reason": reason})
                continue
            if speed_solution is not None:
                adjusted_margin = float("inf")
                for other in traffic:
                    if other is aircraft or getattr(other, "phase", "") not in {"ARRIVAL", "FINAL"}:
                        continue
                    if self.holding_manager.has_active_holding_route(other):
                        continue
                    other_route = self.arrival_routes.get(getattr(other, "arrival_route_id", None))
                    other_speed = getattr(other, "target_speed_kts", None) or getattr(other, "speed_kts", 0)
                    if not isinstance(other_route, dict) or not isinstance(other_speed, (int, float)) or other_speed <= 0:
                        continue
                    other_progress = self._route_progress_nm(other, other_route)
                    if other_progress is None:
                        continue
                    other_etas = self._approach_point_etas(other, other_route, other_progress, float(other_speed))
                    for downstream_index in set(range(index, len(points))).intersection(other_etas):
                        original_own_eta = eta + sum(
                            self._route_segment_length_nm(points[j - 1], points[j]) * 3600.0 / speed
                            for j in range(index + 1, downstream_index + 1)
                        )
                        adjusted_own_eta = original_own_eta * speed / speed_solution
                        own_alt = target_altitude + float(
                            getattr(aircraft, "vertical_speed_fpm", 0) or 0
                        ) * (adjusted_own_eta - eta) / 60.0
                        other_alt = float(other.altitude_ft) + float(
                            getattr(other, "vertical_speed_fpm", 0) or 0
                        ) * other_etas[downstream_index] / 60.0
                        gap = abs(adjusted_own_eta - other_etas[downstream_index]) * max(
                            speed_solution, float(other_speed)
                        ) / 3600.0
                        if gap < self.MIN_MERGE_SEPARATION_NM:
                            unsafe_reason = (
                                f"{other.callsign} remains inside {self.MIN_MERGE_SEPARATION_NM:g} NM at "
                                f"{points[downstream_index].get('id', 'approach fix')} after speed adjustment"
                            )
                            break
                        adjusted_margin = min(adjusted_margin, gap - self.MIN_MERGE_SEPARATION_NM)
                    if unsafe_reason:
                        break
                if unsafe_reason:
                    failures.append(f"{node}: {unsafe_reason}")
                    continue
                minimum_margin = adjusted_margin
            # Route preferences rank already-safe configured entries. They
            # never admit a candidate rejected by the checks above. Within a
            # preference rank, downstream margin, connector length and ETA
            # rank the feasible options. Preserve a safe prior reservation to
            # avoid switching on repeated clearance reassessment.
            rank = preference_rank.get(node, len(preference_rank))
            candidates.append((node == prior_entry, -rank, minimum_margin,
                               -connector_nm, -eta, index, node, speed_solution))
            candidate_diagnostics.append({"node": node, "eligible": True,
                                          "connector_nm": round(connector_nm, 2),
                                          "eta_seconds": round(eta, 1),
                                          "altitude_ft": target_altitude,
                                          "downstream_margin_nm": (None if math.isinf(minimum_margin)
                                                                   else round(minimum_margin, 2))})

        logging.getLogger(__name__).info(
            "Approach candidates evaluated callsign=%s route=%s trigger=%s candidates=%s",
            getattr(aircraft, "callsign", "<unknown>"), getattr(aircraft, "arrival_route_id", None),
            route.get("approach_trigger"), candidate_diagnostics,
        )

        if not candidates:
            trigger_name = str(route.get("approach_trigger", "")).upper()
            compatible_holds = getattr(self.holding_manager, "compatible_holding_options", None)
            holds = compatible_holds(aircraft, trigger_name) if callable(compatible_holds) else []
            hold_choices = [f"HOLD:{item['holding_route_id']}" for item in holds]
            fallback_action = hold_choices[0] if hold_choices else "WAIT"
            resolution_id = ("approach-resolution", *self._decision_session_scope(), str(aircraft.callsign),
                             str(getattr(aircraft, "arrival_route_id", "")), trigger_name)
            resolution_snapshot = {
                "session_scope": self._decision_session_scope(),
                "callsign": str(aircraft.callsign),
                "route_id": str(getattr(aircraft, "arrival_route_id", "")),
                "trigger": trigger_name,
                "phase": str(getattr(aircraft, "phase", "")),
                "route_cursor": getattr(aircraft, "route_index", None),
                "assigned_node": str(getattr(aircraft, "assigned_node", "")),
                "target_node": str(getattr(aircraft, "target_node", "")),
                "navigation_state_version": getattr(aircraft, "navigation_state_version", None),
                "holding_options": tuple(hold_choices),
                "reservations": tuple(sorted(
                    (str(node), str(getattr(owner, "callsign", "")))
                    for node, owner in self.approach_reservations.items() if owner is not aircraft
                )),
                "latitude": float(getattr(aircraft, "lat", 0)),
                "longitude": float(getattr(aircraft, "lon", 0)),
            }
            resolution_prompt = json.dumps({
                "task": "No approach entry passed the deterministic safety checks. Choose one listed compatible hold or WAIT.",
                "aircraft": {"callsign": aircraft.callsign,
                             "type": getattr(aircraft, "aircraft_type", None),
                             "position": [getattr(aircraft, "lat", None), getattr(aircraft, "lon", None)],
                             "altitude_ft": getattr(aircraft, "altitude_ft", None),
                             "speed_kts": getattr(aircraft, "speed_kts", None),
                             "route_id": getattr(aircraft, "arrival_route_id", None),
                             "route": list(getattr(aircraft, "route", []) or []),
                             "next_waypoint": getattr(aircraft, "target_node", None)},
                "approach_candidate_rejections": failures,
                "approach_candidates_considered": candidate_diagnostics,
                "compatible_holds": holds,
                "active_holding_assignments": (self.holding_manager.active_holding_routes()
                                                if callable(getattr(self.holding_manager, "active_holding_routes", None)) else []),
                "available_actions": hold_choices + ["WAIT"],
                "choice_schema": {"choice": "one listed HOLD:<route_id> or WAIT", "reason": "short rationale"},
            }, separators=(",", ":"))
            resolution = (mistral_controller.operational_choice(
                resolution_id, resolution_prompt, hold_choices + ["WAIT"],
                resolution_snapshot, fallback_action,
            ) if getattr(self, "ai_arrival_decisions_enabled", False) else {
                "status": "decided", "choice": fallback_action,
                "source": "deterministic_fallback", "reason": "AI path disabled for this engine instance",
            })
            if resolution["status"] == "pending":
                return {"safe": False, "ai_pending": True,
                        "reason": "Waiting for an AI hold-or-wait decision; no approach entry is safe",
                        "holding_route_id": fallback_action[5:] if fallback_action.startswith("HOLD:") else None,
                        "approach_candidates_considered": candidate_diagnostics}
            if resolution["choice"].startswith("HOLD:"):
                self._record_guidance_event(
                    aircraft, "AI HOLD SELECTED" if resolution.get("source") == "Mistral"
                    else "DETERMINISTIC HOLD SELECTED", trigger_name,
                    f"{resolution.get('reason', '')}; hold={resolution['choice'][5:]}; source={resolution.get('source')}",
                )
                return {"safe": False, "ai_action": "HOLD",
                        "holding_route_id": resolution["choice"][5:],
                        "decision_source": resolution.get("source"),
                        "reason": resolution.get("reason", "Holding selected because no approach entry is safe"),
                        "approach_candidates_considered": candidate_diagnostics}
            self._record_guidance_event(
                aircraft, "AI APPROACH WAIT SELECTED" if resolution.get("source") == "Mistral"
                else "DETERMINISTIC APPROACH WAIT SELECTED", trigger_name,
                f"{resolution.get('reason', '')}; source={resolution.get('source')}",
            )
            rejection_reason = "; ".join(failures) or "Neither approach entry is safe"
            if not any(isinstance(point.get("arrival_entry_transition"), dict)
                       and point["arrival_entry_transition"].get("type") == "DIRECT_FROM_ARRIVAL_TRIGGER"
                       for point in points if isinstance(point, dict)):
                rejection_reason = f"No configured arrival-trigger approach transition; {rejection_reason}"
            result = {"safe": False,
                      "ai_action": "WAIT",
                      "reason": (resolution.get("reason") if resolution.get("source") == "Mistral"
                                 else f"{rejection_reason}; {resolution.get('reason', '')}"),
                      "approach_candidates_considered": candidate_diagnostics}
            if required_hold_delays:
                result["required_delay_seconds"] = max(required_hold_delays)
            return result
        candidates.sort(reverse=True)
        deterministic_candidate = candidates[0]
        safe_entries = [
            {"node": item[6], "index": item[5], "preference_rank": -item[1],
             "predicted_entry_eta_seconds": round(-item[4], 1),
             "downstream_margin_nm": (None if math.isinf(item[2]) else round(item[2], 2)),
             "altitude_ft": float(points[item[5]].get("altitude_ft", 0) or 0)}
            for item in candidates
        ]
        reservation_snapshot = tuple(sorted(
            (str(node).upper(), str(getattr(owner, "callsign", "")))
            for node, owner in self.approach_reservations.items() if owner is not aircraft
        ))
        decision_id = ("approach-entry", *self._decision_session_scope(), str(aircraft.callsign),
                       str(getattr(aircraft, "arrival_route_id", "")),
                       str(route.get("approach_trigger", "")).upper())
        snapshot = {
            "session_scope": self._decision_session_scope(),
            "callsign": str(aircraft.callsign),
            "route_id": str(getattr(aircraft, "arrival_route_id", "")),
            "trigger": str(route.get("approach_trigger", "")).upper(),
            "phase": str(getattr(aircraft, "phase", "")),
            "approach_clearance": bool(getattr(aircraft, "approach_clearance", False)),
            "route_cursor": getattr(aircraft, "route_index", None),
            "assigned_node": str(getattr(aircraft, "assigned_node", "")),
            "target_node": str(getattr(aircraft, "target_node", "")),
            "navigation_state_version": getattr(aircraft, "navigation_state_version", None),
            "safe_nodes": tuple(item["node"] for item in safe_entries),
            "reservations": reservation_snapshot,
            "latitude": float(getattr(aircraft, "lat", 0)),
            "longitude": float(getattr(aircraft, "lon", 0)),
        }
        prompt = json.dumps({
            "task": "Choose one safe, compatible approach entry for this arrival. Select only a listed safe node.",
            "policy": [
                "Honor the route-specific preference rank unless traffic conditions justify another listed safe node.",
                "If APP32 is reserved, consider APP38 when safe and compatible.",
                "If APP38 is assigned to a leading aircraft and APP25 is listed safe for this trailing aircraft, prefer APP25 over APP32.",
                "Do not treat different nodes as proof of separation; the listed candidates already passed the safety checks.",
            ],
            "aircraft": {
                "callsign": aircraft.callsign, "type": getattr(aircraft, "aircraft_type", None),
                "position": {"latitude": aircraft.lat, "longitude": aircraft.lon},
                "altitude_ft": getattr(aircraft, "altitude_ft", None),
                "target_altitude_ft": getattr(aircraft, "target_altitude_ft", None),
                "speed_kts": getattr(aircraft, "speed_kts", None), "route_id": getattr(aircraft, "arrival_route_id", None),
                "route": list(getattr(aircraft, "route", []) or []),
                "route_index": getattr(aircraft, "route_index", None),
                "next_waypoint": getattr(aircraft, "target_node", None),
                "current_entry": prior_entry,
            },
            "arrival_preferences": configured_preferences,
            "entry_candidates_validated_safe": safe_entries,
            "approach_candidates_considered": candidate_diagnostics,
            "existing_entry_reservations": [
                {"node": node, "callsign": owner.callsign,
                 "reservation_status": self.approach_reservation_status.get(node, "active"),
                 "phase": getattr(owner, "phase", None),
                 "approach_index": getattr(owner, "approach_index", None)}
                for node, owner in self.approach_reservations.items()
            ],
            "nearby_arrivals": [
                {"callsign": item.callsign, "position": [item.lat, item.lon],
                 "altitude_ft": item.altitude_ft, "speed_kts": item.speed_kts,
                 "route_id": getattr(item, "arrival_route_id", None),
                 "next_waypoint": getattr(item, "target_node", None),
                 "assigned_entry": getattr(item, "_approach_entry_node", None),
                 "approach_index": getattr(item, "approach_index", None)}
                for item in traffic if item is not aircraft
                and getattr(item, "phase", "") in {"ARRIVAL", "FINAL"}
            ],
            "choice_schema": {"choice": "one listed node ID", "reason": "short rationale"},
        }, separators=(",", ":"))
        ai_choice = (mistral_controller.operational_choice(
            decision_id, prompt, [item["node"] for item in safe_entries], snapshot,
            deterministic_candidate[6],
        ) if getattr(self, "ai_arrival_decisions_enabled", False) else {
            "status": "decided", "choice": deterministic_candidate[6],
            "source": "deterministic_fallback", "reason": "AI path disabled for this engine instance",
        })
        if ai_choice["status"] == "pending":
            provisional_node = deterministic_candidate[6]
            self._release_approach_reservation(aircraft)
            self.approach_reservations[provisional_node] = aircraft
            # The candidate has already passed every deterministic route,
            # altitude, traffic, and separation check. Do not leave the
            # aircraft orbiting its arrival trigger while the optional model
            # preference is still pending; commit this safe deterministic
            # fallback through the normal clearance path.
            self.approach_reservation_status[provisional_node] = "active"
            aircraft._approach_entry_index = deterministic_candidate[5]
            aircraft._approach_entry_node = provisional_node
            self._record_guidance_event(
                aircraft, "APPROACH_CANDIDATE_SELECTED", provisional_node,
                "AI selection pending; committing the highest-ranked deterministic candidate after safety validation",
                decision_source="DETERMINISTIC_PENDING_FALLBACK",
            )
            self._record_guidance_event(
                aircraft, "APPROACH_RESERVATION_GRANTED", provisional_node,
                "Deterministic safety checks passed; reservation committed while AI preference was pending",
                decision_source="DETERMINISTIC_PENDING_FALLBACK",
            )
            return {"safe": True,
                    "reason": "AI preference pending; deterministic safe approach entry committed",
                    "approach_entry_index": deterministic_candidate[5],
                    "approach_entry_node": provisional_node,
                    "decision_source": "DETERMINISTIC_PENDING_FALLBACK",
                    "approach_candidates_considered": candidate_diagnostics}
        selected_candidate = next(
            (item for item in candidates if item[6] == ai_choice["choice"]),
            deterministic_candidate,
        )
        if ai_choice.get("source") == "deterministic_fallback" and prior_entry is not None:
            selected_candidate = next((item for item in candidates if item[6] == prior_entry),
                                      selected_candidate)
        selected_node = selected_candidate[6]
        selected_index = selected_candidate[5]
        preference_score = selected_candidate[1]
        negative_eta = selected_candidate[4]
        speed_solution = selected_candidate[7]
        self._release_approach_reservation(aircraft)
        self.approach_reservations[selected_node] = aircraft
        self.approach_reservation_status[selected_node] = "active"
        eta = -negative_eta
        aircraft._approach_entry_index = selected_index
        aircraft._approach_entry_node = selected_node
        selected_eta = (eta * float(own_speed) / speed_solution
                        if speed_solution is not None else eta)
        decision_reason = ai_choice.get("reason", "Safe route-priority fallback")
        self._record_guidance_event(
            aircraft,
            "APPROACH_CANDIDATE_SELECTED",
            selected_node,
            f"{decision_reason}; source={ai_choice.get('source')}",
            decision_source=ai_choice.get("source"),
        )
        self._record_guidance_event(
            aircraft,
            "APPROACH_RESERVATION_GRANTED",
            selected_node,
            "Deterministic validation passed and entry reservation was committed",
            decision_source=ai_choice.get("source"),
        )
        self._record_guidance_event(
            aircraft,
            "AI APPROACH ENTRY SELECTED" if ai_choice.get("source") == "Mistral"
            else "DETERMINISTIC APPROACH ENTRY SELECTED",
            selected_node,
            f"{decision_reason}; source={ai_choice.get('source')}",
        )
        logging.getLogger(__name__).info(
            "Approach entry selected callsign=%s node=%s source=%s safe_candidates=%s",
            getattr(aircraft, "callsign", "<unknown>"), selected_node,
            ai_choice.get("source"), [item["node"] for item in safe_entries],
        )
        return {"safe": True, "reason": f"{selected_node} selected with downstream separation available: {decision_reason}",
                "approach_entry_index": selected_index, "approach_entry_node": selected_node,
                "approach_entry_preference_rank": -preference_score,
                "predicted_entry_eta_seconds": round(selected_eta, 1),
                "decision_source": ai_choice.get("source"),
                "approach_candidates_considered": candidate_diagnostics,
                **({"speed_reduction_kts": round(speed_solution, 1)}
                   if speed_solution is not None else {})}

    def _approach_entry_eta_seconds(self, aircraft, route, progress, speed, point,
                                    from_fallback=False):
        route_points = route.get("waypoints", [])
        if not route_points or speed <= 0:
            return None
        remaining_route = self._active_route_distance_to_trigger_nm(aircraft, route)
        if remaining_route is not None:
            origin = next((item for item in route_points
                           if str(item.get("name", "")).upper()
                           == str(route.get("approach_trigger", "")).upper()), route_points[-1])
        elif from_fallback:
            remaining_route = 0.0
            origin = {"latitude": aircraft.lat, "longitude": aircraft.lon}
        else:
            remaining_route = sum(self._route_segment_length_nm(a, b)
                                  for a, b in zip(route_points, route_points[1:])) - progress
            origin = route_points[-1]
        connector = self._route_segment_length_nm(
            origin, {"latitude": point["latitude"], "longitude": point["longitude"]}
        )
        if remaining_route < -0.1:
            return None
        return max(0.0, remaining_route + connector) * 3600.0 / speed

    def _prune_approach_reservations(self):
        with self._approach_allocation_lock:
            active = {id(item): item for item in self.manager.all()}
            for node, owner in list(self.approach_reservations.items()):
                if id(owner) not in active or active[id(owner)] is not owner:
                    self._release_approach_reservation(owner)
                    continue
                # A hold assignment suspends approach sequencing ownership.
                # Retaining its old entry reservation blocks another arrival
                # even though the held aircraft has not committed that entry.
                if self.holding_manager.has_active_holding_route(owner):
                    self._release_approach_reservation(owner)
                    continue
                entry_index = getattr(owner, "_approach_entry_index", None)
                if (getattr(owner, "phase", "") not in {"ARRIVAL", "FINAL"}
                        or (isinstance(entry_index, int)
                            and isinstance(getattr(owner, "approach_index", None), int)
                            and owner.approach_index > entry_index)):
                    self._release_approach_reservation(owner)

            # Recover ownership from a clearance already handed to ApproachAI
            # if a caller restored aircraft state without the in-memory map.
            # A reservation already owned by another aircraft remains
            # authoritative and is never overwritten here.
            configured_entries = {
                str(point.get("id", "")).upper()
                for point in self.approach_ai.approach_points
                if isinstance(point.get("arrival_entry_transition"), dict)
                and point["arrival_entry_transition"].get("type")
                == "DIRECT_FROM_ARRIVAL_TRIGGER"
            }
            for aircraft in active.values():
                node = str(getattr(aircraft, "_approach_entry_node", "")).upper()
                entry_index = getattr(aircraft, "_approach_entry_index", None)
                if ((node not in configured_entries or not isinstance(entry_index, int))
                        and getattr(aircraft, "phase", "") == "FINAL"
                        and getattr(aircraft, "approach_clearance", False)):
                    active_target = str(getattr(aircraft, "target_node", "")).upper()
                    target_entry = next((
                        (index, str(point.get("id", "")).upper())
                        for index, point in enumerate(self.approach_ai.approach_points)
                        if isinstance(point, dict)
                        and str(point.get("id", "")).upper() == active_target
                        and active_target in configured_entries
                    ), None)
                    if target_entry is not None:
                        entry_index, node = target_entry
                        aircraft._approach_entry_index = entry_index
                        aircraft._approach_entry_node = node
                if (node not in configured_entries
                        or not isinstance(entry_index, int)
                        or getattr(aircraft, "phase", "") not in {"ARRIVAL", "FINAL"}):
                    continue
                if (isinstance(getattr(aircraft, "approach_index", None), int)
                        and aircraft.approach_index > entry_index):
                    continue
                owner = self.approach_reservations.get(node)
                if owner is None or owner is aircraft:
                    self.approach_reservations[node] = aircraft

    def _release_approach_reservation(self, aircraft):
        for node, owner in list(self.approach_reservations.items()):
            if owner is aircraft:
                self.approach_reservations.pop(node, None)
                getattr(self, "approach_reservation_status", {}).pop(node, None)
        for name in ("_approach_entry_index", "_approach_entry_node"):
            if hasattr(aircraft, name):
                delattr(aircraft, name)


    def _record_hold_release(self, aircraft, release):
        safety = release.get("safety", {})
        route_id = aircraft.arrival_route_id
        route = self.arrival_routes.get(route_id, {}).get("waypoints", [])
        rejoin = release.get("rejoin_waypoint")
        rejoin_index = next((i for i, point in enumerate(route)
                             if str(point.get("name", "")).upper() == str(rejoin).upper()), None)
        next_node = route[rejoin_index + 1]["name"] if rejoin_index is not None and rejoin_index + 1 < len(route) else None
        self.ai_decision_log.insert(0, {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "callsign": aircraft.callsign, "controller": "Arrival", "decision": "RELEASE HOLD",
            "instruction": "Resume the permanent arrival route", "conflict_waypoint": rejoin,
            "other_aircraft": safety.get("other_aircraft"), "altitude": aircraft.altitude_ft,
            "speed": aircraft.speed_kts, "holding_route_id": release.get("holding_route_id"),
            "hold_circuits": release.get("completed_circuits"), "rejoin_node": next_node,
            "reason": safety.get("reason", "Downstream route clear and required separation available"),
            "decision_source": safety.get("decision_source", "DETERMINISTIC_SAFETY_VALIDATION"),
            "old_state": release.get("old_state"), "new_state": release.get("new_state"),
            "old_route": release.get("old_route"),
            "old_route_index": release.get("old_route_index"),
            "old_target_node": release.get("old_target_node"),
            "active_route": list(getattr(aircraft, "route", []) or []),
            "route_index": getattr(aircraft, "route_index", None),
            "assigned_waypoint": getattr(aircraft, "assigned_node", None),
            "target_waypoint": getattr(aircraft, "target_node", None),
            "clearance_direct_node": getattr(getattr(aircraft, "clearance", None), "direct_node", None),
            "hold_active": release.get("hold_active"),
            "approach_entry_node": release.get("approach_entry_node"),
            "reservation_state": [
                {"node": node, "status": self.approach_reservation_status.get(node, "active")}
                for node, owner in getattr(self, "approach_reservations", {}).items()
                if owner is aircraft
            ],
        })
        del self.ai_decision_log[50:]

    def _restore_shared_waypoint_speeds(self, aircraft_list):

        for aircraft in aircraft_list:
            if hasattr(aircraft, "_sequence_speed_ceiling"):
                del aircraft._sequence_speed_ceiling
            applied_altitude = getattr(aircraft, "_separation_applied_target_altitude", None)
            if applied_altitude is not None:
                if getattr(aircraft, "target_altitude_ft", None) == applied_altitude:
                    original_altitude = getattr(aircraft, "_separation_original_target_altitude", None)
                    if original_altitude is not None:
                        aircraft.assign_altitude(original_altitude)
                del aircraft._separation_applied_target_altitude
                if hasattr(aircraft, "_separation_original_target_altitude"):
                    del aircraft._separation_original_target_altitude
                if getattr(aircraft, "separation_status", None) == "CRITICAL CONFLICT":
                    del aircraft.separation_status

            predictive_target = getattr(aircraft, "_engine_predictive_applied_target_speed", None)
            if predictive_target is not None:
                if getattr(aircraft, "target_speed_kts", None) == predictive_target:
                    aircraft.assign_speed(aircraft._engine_predictive_original_target_speed)
                del aircraft._engine_predictive_applied_target_speed
                del aircraft._engine_predictive_original_target_speed

            applied_target = getattr(
                aircraft,
                "_engine_shared_waypoint_applied_target_speed",
                None
            )
            if applied_target is None:
                continue

            if getattr(aircraft, "target_speed_kts", None) == applied_target:
                aircraft.assign_speed(aircraft._engine_shared_waypoint_original_target_speed)

            del aircraft._engine_shared_waypoint_applied_target_speed
            del aircraft._engine_shared_waypoint_original_target_speed

    def _apply_shared_waypoint_separation(self, aircraft_list):

        shared_waypoint_groups = self.shared_arrival_waypoint_groups
        if not shared_waypoint_groups:
            return

        arrivals = []
        for aircraft in aircraft_list:
            route_id = getattr(aircraft, "arrival_route_id", None)
            if (
                getattr(aircraft, "phase", "") not in ("ARRIVAL", "FINAL")
                or route_id not in self.arrival_routes
                or self.holding_manager.has_active_holding_route(aircraft)
            ):
                continue

            route = self.arrival_routes[route_id]
            if not isinstance(route, dict):
                continue

            try:
                progress = self._route_progress_nm(aircraft, route)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue

            speed = getattr(aircraft, "speed_kts", None)
            target = getattr(aircraft, "target_speed_kts", None)
            if (
                progress is not None
                and speed is not None
                and speed > 0
                and target is not None
                and route.get("waypoints")
            ):
                arrivals.append((aircraft, route_id, route, progress, speed, target))

        if len(arrivals) < 2:
            return

        desired_targets = {}
        for group in shared_waypoint_groups:
            for first_index in range(len(arrivals)):
                first = arrivals[first_index]
                first_occurrences = [
                    occurrence
                    for occurrence in group
                    if occurrence[0] == first[1]
                    and occurrence[2] - first[3] >= -4.5
                ]
                if not first_occurrences:
                    continue

                first_occurrence = min(
                    first_occurrences,
                    key=lambda occurrence: occurrence[2] - first[3]
                )
                first_remaining = first_occurrence[2] - first[3]

                for second_index in range(first_index + 1, len(arrivals)):
                    second = arrivals[second_index]
                    if first[1] == second[1]:
                        continue

                    second_occurrences = [
                        occurrence
                        for occurrence in group
                        if occurrence[0] == second[1]
                        and occurrence[2] - second[3] >= -4.5
                    ]
                    if not second_occurrences:
                        continue

                    second_occurrence = min(
                        second_occurrences,
                        key=lambda occurrence: occurrence[2] - second[3]
                    )
                    second_remaining = second_occurrence[2] - second[3]
                    if max(first_remaining, second_remaining) > self.SEQUENCING_WINDOW_NM:
                        continue

                    if first_remaining <= second_remaining:
                        leader, leader_remaining = first, first_remaining
                        follower, follower_remaining = second, second_remaining
                    else:
                        leader, leader_remaining = second, second_remaining
                        follower, follower_remaining = first, first_remaining

                    if leader_remaining < -4.5 or follower_remaining <= 0:
                        continue

                    gap = follower_remaining - leader_remaining
                    minimum_speed = self._minimum_sequencing_speed(follower[0])
                    if gap >= self.MIN_MERGE_SEPARATION_NM or follower[5] <= minimum_speed:
                        continue

                    adjusted = max(
                        minimum_speed,
                        float(follower[5]) - self.MAX_SEQUENCING_SPEED_DELTA_KTS,
                        leader[4] - (self.MIN_MERGE_SEPARATION_NM - gap) * 12.0
                    )
                    current_target = desired_targets.get(id(follower[0]), follower[5])
                    desired_targets[id(follower[0])] = min(
                        current_target,
                        adjusted
                    )

        for aircraft, _, _, _, _, target in arrivals:
            adjusted = desired_targets.get(id(aircraft), target)
            if adjusted < target:
                if not hasattr(aircraft, "_engine_shared_waypoint_original_target_speed"):
                    aircraft._engine_shared_waypoint_original_target_speed = target
                aircraft.target_speed_kts = max(self._minimum_sequencing_speed(aircraft), adjusted)
                aircraft._sequence_speed_ceiling = aircraft.target_speed_kts
                aircraft._engine_shared_waypoint_applied_target_speed = aircraft.target_speed_kts

    def process_due_departures(self):

        if (getattr(self, "session_mode", "NORMAL") == "TEST"
                or not getattr(self, "normal_generation_allowed", lambda: True)()):
            return []

        active_count = len(self.manager.all())
        wave_name = self.traffic_manager.active_wave[0]
        departure_gaps = {
            "LOW": (75, 115),
            "MEDIUM": (50, 85),
            "HIGH": (38, 68),
        }
        departures = self.scheduler.get_due_departures(
            active_aircraft_count=active_count,
            active_capacity=self.traffic_manager.active_wave_capacity,
            release_gap_range=departure_gaps.get(wave_name),
        )

        if not departures:
            self._record_departure_diagnostic(
                getattr(self.scheduler, "last_departure_status", "no_due_aircraft"),
                active_aircraft=active_count,
                active_capacity=min(self.traffic_manager.active_wave_capacity,
                                    getattr(self.scheduler, "MAX_ACTIVE_AIRCRAFT", active_count)),
            )

        for aircraft_data in departures:

            callsign = aircraft_data["callsign"]

            print(
                f"DEPARTURE DUE: "
                f"{callsign} | "
                f"{aircraft_data['airline']} | "
                f"VABB -> {aircraft_data['destination']}"
            )

            if any(a.callsign == callsign for a in self.manager.all()):
                continue
            route = self._departure_route_for(aircraft_data["destination"])
            if route is None:
                print(f"DEPARTURE ROUTE UNAVAILABLE: {callsign} -> {aircraft_data['destination']}")
                self._record_departure_diagnostic(
                    "missing_or_incompatible_sid", callsign=callsign,
                    destination=aircraft_data["destination"],
                    aircraft_type=aircraft_data["aircraft_type"],
                )
                continue
            points = route["waypoints"]
            if not points:
                self._record_departure_diagnostic(
                    "empty_sid_route", callsign=callsign, route_id=route.get("route_id"),
                )
                continue
            first = points[0]
            aircraft = spawn_aircraft(callsign, aircraft_data["aircraft_type"], first["latitude"], first["longitude"])
            aircraft.phase = "DEPARTURE"
            departure_profile = match_departure_profile(
                route, aircraft_data["aircraft_type"], getattr(self, "departure_profiles", {})
            )
            runway_profile = departure_profile[0] if departure_profile else None
            initial_altitude = (runway_profile.get("alt_ft") if runway_profile
                                else first.get("altitude", {}).get("feet", 20))
            aircraft.altitude_ft = float(initial_altitude if initial_altitude is not None else 20)
            aircraft.target_altitude_ft = aircraft.altitude_ft
            aircraft.speed_kts = 0.0
            roll_speed = (runway_profile.get("ias_kt") if runway_profile
                          else 150.0)
            aircraft.target_speed_kts = float(roll_speed or 150)
            aircraft.on_ground = True
            aircraft.state = "TAKEOFF_ROLL"
            aircraft.departure_profile = departure_profile
            aircraft.departure_profile_available = departure_profile is not None
            if departure_profile is None:
                logging.getLogger(__name__).warning(
                    "Departure profile unavailable route=%s name=%s aircraft=%s; using route fallback",
                    route.get("route_id"), route.get("name"), aircraft_data["aircraft_type"],
                )
                self._record_departure_diagnostic(
                    "missing_profile_route_fallback_used", callsign=callsign,
                    route_id=route.get("route_id"), route_name=route.get("name"),
                    aircraft_type=aircraft_data["aircraft_type"],
                )
            aircraft.destination = aircraft_data["destination"]
            aircraft.assign_route([p["name"] for p in points])
            aircraft.departure_waypoints = points
            aircraft.departure_route_id = route["route_id"]
            self.scheduler.db.mark_departing(callsign)
            self.manager.add(aircraft)
            self._record_departure_diagnostic(
                "spawned_on_runway", callsign=callsign, route_id=route["route_id"],
                aircraft_type=aircraft_data["aircraft_type"], profile_available=departure_profile is not None,
            )
            print(f"DEPARTURE SPAWNED: {callsign} route {route['route_id']}")

    def _record_departure_diagnostic(self, status, **details):
        if not hasattr(self, "departure_diagnostics"):
            self.departure_diagnostics = []
        self.departure_diagnostics.insert(0, {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": str(status), **details,
        })
        del self.departure_diagnostics[50:]

    def _departure_route_for(self, destination):
        routes = list(self.departure_routes.values())
        if not routes:
            return None
        destination = str(destination or "").upper()
        mapped_route_id = getattr(self, "departure_destination_map", {}).get(destination)
        if mapped_route_id:
            route = self.departure_routes.get(str(mapped_route_id))
            if (isinstance(route, dict) and route.get("waypoints")
                    and str(route.get("runway", "27")).upper()
                    == str(getattr(self.runway, "ident", "27")).upper()):
                return route
            logging.getLogger(__name__).error(
                "Departure destination map references unavailable route destination=%s route=%s",
                destination, mapped_route_id,
            )
            return None
        matching = [r for r in routes if destination in r.get("name", "").upper()
                    or destination == r["waypoints"][-1]["name"].upper()]
        if matching:
            return min(matching, key=lambda route: route["route_id"])
        return None

    def update(
        self,
        dt=1.0
    ):

        self.spawn_due_arrivals(dt)

        self.process_due_departures()

        traffic = self.manager.all()
        prune_holds = getattr(getattr(self, "holding_manager", None), "prune_inactive_holds", None)
        if callable(prune_holds):
            prune_holds(traffic)
        self._restore_shared_waypoint_speeds(traffic)
        self._apply_shared_waypoint_separation(traffic)
        self._detect_shared_waypoint_conflicts(traffic)
        self._enforce_predictive_separation(traffic)
        apply_arrival_separation(traffic, self.graph)
        self._record_route_following_actions(traffic)

        for aircraft in traffic:
            try:
                if aircraft.phase == "DEPARTURE":
                    self.guidance.update(aircraft, dt)
                    if aircraft.target_node is None:
                        aircraft.lat = self.departure_waypoints[aircraft.assigned_node]["latitude"]
                        aircraft.lon = self.departure_waypoints[aircraft.assigned_node]["longitude"]
                        self.manager.remove(aircraft)
                        self.scheduler.aircraft_completed(aircraft.callsign)
                        print(f"DEPARTURE COMPLETED: {aircraft.callsign} | NEXT ARRIVAL SCHEDULED")
                        continue
                    self._validate_departure_fix_constraints(aircraft)
                    if getattr(aircraft, "on_ground", False):
                        if aircraft.speed_kts >= 140.0:
                            aircraft.on_ground = False
                            aircraft.state = "CLIMB"
                        else:
                            aircraft.assign_speed(
                                float((aircraft.departure_profile or [{}])[0].get("ias_kt") or 150)
                                if getattr(aircraft, "departure_profile_available", False)
                                else 150.0
                            )
                            continue
                    profile = getattr(aircraft, "departure_profile", None)
                    row = next((item for item in profile or []
                                if str(item.get("waypoint", "")).upper()
                                == str(aircraft.target_node).upper()), None)
                    if row is not None:
                        altitude = row.get("alt_ft")
                        if isinstance(altitude, (int, float)):
                            constraint = row.get("pub_alt_constraint")
                            if constraint and constraint.get("unit") in {"ft", "feet"}:
                                required = float(constraint["value"])
                                operator = constraint.get("operator")
                                if operator in {"<=", "<"}:
                                    altitude = min(float(altitude), required)
                                elif operator in {">=", ">"}:
                                    altitude = max(float(altitude), required)
                                elif operator in {"=", "@"}:
                                    altitude = required
                            constraints = [item.get("pub_alt_constraint") for item in profile
                                           if item.get("waypoint") == "RWY27"]
                            departure_minimum = next((item["value"] for item in constraints
                                                      if item and item.get("operator") in {">=", ">"}
                                                      and item.get("unit") in {"ft", "feet"}), None)
                            if (str(row.get("waypoint")).upper() == "BB154"
                                    and departure_minimum is not None):
                                aircraft.departure_climbout_minimum_ft = departure_minimum
                            aircraft.assign_altitude(altitude)
                        target_speed = row.get("gs_kt")
                        if isinstance(target_speed, (int, float)) and target_speed > 0:
                            target_altitude = getattr(
                                aircraft, "target_altitude_ft", aircraft.altitude_ft
                            )
                            indicated_speed = row.get("ias_kt")
                            if isinstance(indicated_speed, (int, float)) and indicated_speed > 0:
                                aircraft.departure_target_ias_kts = float(indicated_speed)
                                target_speed = min(
                                    float(target_speed),
                                    indicated_to_true_airspeed(indicated_speed, target_altitude),
                                )
                            aircraft.assign_speed(float(target_speed))
                        speed_limit = departure_speed_limit_gs(
                            row.get("pub_speed_constraint"),
                            getattr(aircraft, "target_altitude_ft", aircraft.altitude_ft),
                        )
                        if speed_limit is not None:
                            aircraft.assign_speed(min(float(aircraft.target_speed_kts), speed_limit))
                        vertical_speed = row.get("vs_fpm")
                        if not isinstance(vertical_speed, (int, float)):
                            climb_gradient = row.get("climb_ft_per_nm")
                            if isinstance(climb_gradient, (int, float)):
                                vertical_speed = float(climb_gradient) * max(
                                    float(getattr(aircraft, "speed_kts", 0)), 0.0
                                ) / 60.0
                        if isinstance(vertical_speed, (int, float)):
                            aircraft.target_vertical_speed_fpm = float(vertical_speed)
                            aircraft.departure_max_climb_rate_fpm = max(
                                1500.0, abs(float(vertical_speed))
                            )
                        aircraft.departure_climb_ft_per_nm = row.get("climb_ft_per_nm")
                    else:
                        waypoint = next((p for p in aircraft.departure_waypoints
                                         if p["name"] == aircraft.target_node), None)
                        if waypoint:
                            altitude = waypoint.get("altitude", {})
                            if altitude.get("feet") is not None:
                                aircraft.assign_altitude(altitude["feet"])
                            speed = waypoint.get("speed", {})
                            if speed.get("knots") is not None:
                                aircraft.assign_speed(speed["knots"])
                    continue

                self.arrival_ai.update(aircraft)
                self.guidance.update(aircraft, dt)

                if aircraft.phase == "FINAL":
                    self.approach_ai.update(aircraft)

                if getattr(aircraft, "approach_complete", False):
                    self.aircraft_db.upsert(
                        callsign=aircraft.callsign,
                        aircraft_type=aircraft.aircraft_type,
                        runway_exit="27",
                        taxiway="23ft"
                    )
                    self.scheduler.aircraft_landed(aircraft.callsign)
                    print(f"FINAL APPROACH COMPLETE: {aircraft.callsign} -> RWY 27 / 23ft")
                    print(f"TRAFFIC STORED AT AIRPORT: {aircraft.callsign}")
                    self.manager.remove(aircraft)
                    print(f"AIRCRAFT REMOVED FROM SIM: {aircraft.callsign}")
                    continue

                    self.landing_ai.update(aircraft)
                    for arrival_aircraft in traffic:
                        self.guidance.apply_route_constraints(arrival_aircraft)

            except Exception as error:
                print(f"AIRCRAFT UPDATE ERROR {aircraft.callsign}: {error}")

        # Advance every aircraft that remains active exactly once per tick,
        # after this tick's decisions and clearance changes have been applied.
        for aircraft in list(self.manager.all()):
            try:
                move_aircraft(aircraft, dt)
            except Exception as error:
                print(f"AIRCRAFT MOVEMENT ERROR {aircraft.callsign}: {error}")

        self._prune_approach_reservations()

    def _validate_departure_fix_constraints(self, aircraft):
        profile = getattr(aircraft, "departure_profile", None)
        if not profile:
            return
        fix = str(getattr(aircraft, "assigned_node", "")).upper()
        if not fix or fix == "RWY27":
            return
        checked = getattr(aircraft, "_validated_departure_fixes", set())
        if fix in checked:
            return
        checked.add(fix)
        aircraft._validated_departure_fixes = checked
        row = next((item for item in profile
                    if str(item.get("waypoint", "")).upper() == fix), None)
        if row is None:
            return
        constraints = []
        if fix == "BB154":
            runway = next((item for item in profile
                           if str(item.get("waypoint", "")).upper() == "RWY27"), None)
            if runway and runway.get("pub_alt_constraint"):
                constraints.append(("altitude", runway["pub_alt_constraint"], "RWY27 climb-out"))
        if row.get("pub_alt_constraint"):
            constraints.append(("altitude", row["pub_alt_constraint"], fix))
        if row.get("pub_speed_constraint"):
            constraints.append(("speed", row["pub_speed_constraint"], fix))
        results = {}
        for kind, constraint, source in constraints:
            value = (float(getattr(aircraft, "altitude_ft", 0) or 0)
                     if kind == "altitude" else true_to_indicated_airspeed(
                         float(getattr(aircraft, "speed_kts", 0) or 0),
                         float(getattr(aircraft, "altitude_ft", 0) or 0),
                     ))
            required = float(constraint["value"])
            operator = constraint.get("operator")
            passed = {
                ">=": value >= required, ">": value > required,
                "<=": value <= required, "<": value < required,
                "=": abs(value - required) <= (100.0 if kind == "altitude" else 1.0),
                "@": abs(value - required) <= (100.0 if kind == "altitude" else 1.0),
            }.get(operator, False)
            key = f"{kind}:{source}"
            results[key] = {"passed": passed, "actual": round(value, 1),
                            "operator": operator, "required": required,
                            "unit": constraint.get("unit")}
            if not passed:
                logging.getLogger(__name__).warning(
                    "Departure published constraint not met callsign=%s route=%s fix=%s source=%s kind=%s actual=%s %s required=%s %s",
                    aircraft.callsign, getattr(aircraft, "departure_route_id", None), fix,
                    source, kind, round(value, 1), constraint.get("unit"), operator,
                    required, constraint.get("unit"),
                )
        aircraft.departure_constraint_checks = results
