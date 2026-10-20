import json
import math
import random
from pathlib import Path
from datetime import datetime

from app.simulation.manager import AircraftManager
from app.simulation.holding_manager import HoldingManager
from app.simulation.spawn import spawn_aircraft
from app.simulation.physics import move_aircraft
from app.simulation.aircraft_db import AircraftDatabase
from app.simulation.traffic_manager import TrafficManager
from app.simulation.traffic_scheduler import TrafficScheduler
from app.aircraft.aircraft_type import get_aircraft_performance

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
from app.navigation.loader import load_arrival_profiles, load_arrival_routes, load_departure_routes


class SimulationEngine:

    SEQUENCING_WINDOW_NM = 35.0
    MIN_MERGE_SEPARATION_NM = 5.0
    MIN_VERTICAL_SEPARATION_FT = 1000
    CONFLICT_WARNING_DISTANCE_NM = 6.0
    PREDICTION_LOOKAHEAD_SECONDS = 120.0
    CRITICAL_SEPARATION_NM = 1.0

    def __init__(
        self,
        airport_lat=19.0887,
        airport_lon=72.8679
    ):

        self.manager = AircraftManager()

        self.aircraft_db = AircraftDatabase()

        self.traffic_manager = TrafficManager()

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
        # Approach entry ownership is held until the aircraft passes its
        # assigned entry fix. The simulation updates these reservations on its
        # single control thread, so a check and assignment are atomic here.
        self.approach_reservations = {}
        self.ai_decision_log = []
        self._last_separation_event_at = {}

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

        # Temporary sequencing fixes are navigable graph nodes, but are not
        # inserted into permanent arrival routes.
        self.graph.add_node("DOGAP", 18.809143, 73.416653)
        self.graph.add_node("MB398", 19.453401, 73.462572)

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
        )

        self.approach_ai = ApproachAI(
            self.runway
        )

        self.landing_ai = LandingAI(
            self.runway,
            self.graph
        )

    def spawn(
        self,
        aircraft_type,
        callsign,
        route_id
    ):

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

    def spawn_due_arrivals(self, dt=1.0):

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

        travelled_to_end = sum(self._route_segment_length_nm(a, b)
                               for a, b in zip(route_points, route_points[1:]))
        endpoint = route_points[-1]
        start_index = min(range(len(approach_points)),
                          key=lambda index: self._route_segment_length_nm(endpoint, approach_points[index]))
        distance = max(0.0, travelled_to_end - progress)
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
        if required_speed < minimum_speed or required_speed >= target - 1.0:
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
        minimum = float(performance.get("minimum_arrival_speed_kts", 170))
        if getattr(aircraft, "phase", "") == "FINAL":
            index = getattr(aircraft, "approach_index", None)
            points = self.approach_ai.approach_points
            if isinstance(index, int) and 0 <= index < len(points):
                profile_speed = self.approach_ai._get_speed(aircraft, points[index].get("id"))
                if isinstance(profile_speed, (int, float)):
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
        first_profile = (route_profiles.get(str(first.get("name", "")).upper(), {})
                         if isinstance(route_profiles, dict) else {})
        altitude = first_profile.get("altitude")
        altitude_ft = altitude.get("feet") if isinstance(altitude, dict) else altitude
        if isinstance(altitude_ft, (int, float)) and not isinstance(altitude_ft, bool):
            aircraft.altitude_ft = altitude_ft
            aircraft.target_altitude_ft = altitude_ft

        speed = first_profile.get("speed")
        speed_kts = speed.get("knots") if isinstance(speed, dict) else speed
        if isinstance(speed_kts, (int, float)) and not isinstance(speed_kts, bool):
            aircraft.speed_kts = speed_kts
            aircraft.target_speed_kts = speed_kts

        print(f"ARRIVAL SPAWN: {aircraft.callsign} route={aircraft.arrival_route_id} "
              f"spawn={first['name']} index=0 lat={aircraft.lat} lon={aircraft.lon} "
              f"alt={aircraft.altitude_ft}")

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
                    or mistral_controller.has_pending_conflict_for(aircraft.callsign)):
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
                    if abs(first_altitude - second_altitude) >= self.MIN_VERTICAL_SEPARATION_FT:
                        continue
                    gap_nm = abs(first_eta - second_eta) * max(first[4], second[4]) / 3600.0
                    if gap_nm < self.MIN_MERGE_SEPARATION_NM:
                        candidates.append((max(first_eta, second_eta), point_index,
                                           first_eta, second_eta, gap_nm))
                if not candidates:
                    continue
                _, approach_index, first_eta, second_eta, gap_nm = min(candidates)
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
                or mistral_controller.has_pending_conflict_for(aircraft.callsign)
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
                            if max(first_remaining, second_remaining) > self.SEQUENCING_WINDOW_NM:
                                continue
                            second_eta_seconds = second_remaining * 3600.0 / second[3]
                            prediction_time = max(first_eta_seconds, second_eta_seconds)
                            first_altitude = (float(getattr(first[0], "altitude_ft", 0))
                                              + float(getattr(first[0], "vertical_speed_fpm", 0)) * prediction_time / 60.0)
                            second_altitude = (float(getattr(second[0], "altitude_ft", 0))
                                               + float(getattr(second[0], "vertical_speed_fpm", 0)) * prediction_time / 60.0)
                            vertical_separation = abs(first_altitude - second_altitude)
                            if vertical_separation >= self.MIN_VERTICAL_SEPARATION_FT:
                                continue
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
            key = mistral_controller.conflict_key(conflict)
            unique_conflicts.setdefault(key, conflict)
        active_holds = {
            aircraft.callsign for aircraft in aircraft_list
            if self.holding_manager.has_active_holding_route(aircraft)
        }
        mistral_controller.clear_conflicts(set(unique_conflicts), active_holds)
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
            "decision_source": result.get("decision_source", "Mistral"),
            "aircraft_ahead": conflict.get("predicted_timing", {}).get("aircraft_ahead"),
            "aircraft_behind": conflict.get("predicted_timing", {}).get("aircraft_behind"),
            "current_separation_nm": conflict.get("predicted_timing", {}).get("current_separation_nm"),
            "relative_closure_rate_kts": conflict.get("predicted_timing", {}).get("relative_closure_rate_kts"),
            "predicted_merge_separation_nm": conflict.get("predicted_timing", {}).get("predicted_separation_nm"),
            "prediction_horizon_seconds": conflict.get("predicted_timing", {}).get("prediction_horizon_seconds"),
            "aircraft_timing": conflict.get("aircraft_timing", {}),
        })
        del self.ai_decision_log[50:]

    def _record_guidance_event(self, aircraft, event, waypoint, reason):
        event_key = (str(aircraft.callsign), str(event), str(waypoint))
        now = datetime.now().astimezone()
        previous = self._last_separation_event_at.get(event_key)
        if previous is not None and (now - previous).total_seconds() < 30:
            return
        self._last_separation_event_at[event_key] = now
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
        for first_index, first in enumerate(traffic):
            for second in traffic[first_index + 1:]:
                mean_latitude = math.radians((first.lat + second.lat) / 2.0)
                east = (second.lon - first.lon) * 60.0 * math.cos(mean_latitude)
                north = (second.lat - first.lat) * 60.0
                first_speed = max(0.0, float(getattr(first, "speed_kts", 0) or 0)) / 3600.0
                second_speed = max(0.0, float(getattr(second, "speed_kts", 0) or 0)) / 3600.0
                first_heading = math.radians(float(getattr(first, "heading_deg", 0) or 0))
                second_heading = math.radians(float(getattr(second, "heading_deg", 0) or 0))
                relative_east = second_speed * math.sin(second_heading) - first_speed * math.sin(first_heading)
                relative_north = second_speed * math.cos(second_heading) - first_speed * math.cos(first_heading)
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
                if vertical_difference >= self.MIN_VERTICAL_SEPARATION_FT:
                    continue
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
                    first_eta = (self._approach_merge_eta_seconds(first, first_route, first_progress, first_speed * 3600.0)
                                 if first_progress is not None else None)
                    second_eta = (self._approach_merge_eta_seconds(second, second_route, second_progress, second_speed * 3600.0)
                                  if second_progress is not None else None)
                    if first_eta is not None and second_eta is not None:
                        yielding, leader = (first, second) if first_eta >= second_eta else (second, first)
                    else:
                        yielding, leader = (first, second) if first_speed <= second_speed else (second, first)

                target = getattr(yielding, "target_speed_kts", None)
                leader_speed = float(getattr(leader, "speed_kts", 0) or 0)
                minimum_speed = self._minimum_sequencing_speed(yielding)
                if isinstance(target, (int, float)) and target > minimum_speed:
                    deficit = self.MIN_MERGE_SEPARATION_NM - closest_distance
                    adjusted = max(minimum_speed,
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
                    for option in options:
                        result = self.holding_manager.apply_holding_route(
                            yielding, option["holding_route_id"], circuits=1
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

    def _can_release_holding(self, aircraft):
        hold = self.holding_manager.state_for(aircraft)
        if hold is None:
            return {"safe": False, "reason": "Active holding state is missing"}
        priority_callsign = hold.get("priority_callsign")
        protected_merge_point = hold.get("protected_merge_point")
        if priority_callsign and protected_merge_point:
            priority = next((item for item in self.manager.all()
                             if str(item.callsign) == str(priority_callsign)), None)
            if priority is None:
                return {"safe": True, "reason": "Priority aircraft has cleared the protected merge point"}
            priority_route = self.arrival_routes.get(getattr(priority, "arrival_route_id", None), {})
            priority_points = priority_route.get("waypoints", []) if isinstance(priority_route, dict) else []
            merge_index = next((index for index, point in enumerate(priority_points)
                                if str(point.get("name", "")).upper()
                                == str(protected_merge_point).upper()), None)
            priority_index = getattr(priority, "route_index", None)
            if (merge_index is not None and isinstance(priority_index, int)
                    and priority_index > merge_index
                    and not self.holding_manager.has_active_holding_route(priority)):
                return {"safe": True,
                        "reason": f"Priority aircraft cleared protected merge point {protected_merge_point}",
                        "other_aircraft": priority_callsign,
                        "conflict_waypoint": protected_merge_point}
            return {"safe": False,
                    "reason": f"Waiting for priority aircraft {priority_callsign} to clear {protected_merge_point}",
                    "other_aircraft": priority_callsign,
                    "conflict_waypoint": protected_merge_point}
        if mistral_controller.has_pending_conflict_for(aircraft.callsign):
            return {"safe": False, "reason": "A traffic-conflict decision is still pending"}
        route_id = getattr(aircraft, "arrival_route_id", None)
        route_definition = self.arrival_routes.get(route_id, {})
        route_points = route_definition.get("waypoints", [])
        route_names = hold["original_route"]
        fix_name = str(hold["rejoin_waypoint"]).upper()
        fix_index = next((i for i, name in enumerate(route_names) if str(name).upper() == fix_name), None)
        if fix_index is None:
            return {"safe": False, "reason": "Holding fix is not on the permanent route"}

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
            return {"safe": False, "reason": "Permanent route coordinates are incomplete"}
        own_speed = getattr(aircraft, "target_speed_kts", None) or getattr(aircraft, "speed_kts", 0)
        own_altitude = getattr(aircraft, "altitude_ft", 0)
        if not isinstance(own_speed, (int, float)) or own_speed <= 0:
            return {"safe": False, "reason": "No valid speed for rejoin prediction"}

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
                return {"safe": False, "reason": f"{other.callsign} route coordinates are unavailable"}
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
                        own_wait_started = hold.get("holding_since", float("inf"))
                        other_wait_started = other_state.get("holding_since", float("inf"))
                        if own_wait_started <= other_wait_started:
                            continue
                        return {"safe": False, "reason": f"{other.callsign} has waited longer and is ready to rejoin the downstream route",
                                "other_aircraft": other.callsign, "conflict_waypoint": name}
                    time_gap = abs(held_eta - (other_cumulative[other_index] - other_progress) * 3600.0 / other_speed)
                    other_altitude = getattr(other, "altitude_ft", 0)
                    if abs(own_altitude - other_altitude) >= 1000:
                        continue
                    predicted_gap_nm = time_gap * max(float(own_speed), float(other_speed)) / 3600.0
                    if predicted_gap_nm < self.MIN_MERGE_SEPARATION_NM:
                        return {"safe": False,
                                "reason": f"{other.callsign} is predicted to merge at {name}",
                                "other_aircraft": other.callsign,
                                "conflict_waypoint": name,
                                "predicted_separation_nm": round(predicted_gap_nm, 2)}

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
                if abs(own_altitude - getattr(other, "altitude_ft", 0)) >= self.MIN_VERTICAL_SEPARATION_FT:
                    continue
                final_approach_fix = (self.approach_ai.approach_points[-1].get("id")
                                      if self.approach_ai.approach_points else "downstream approach")
                return {"safe": False,
                        "reason": f"{other.callsign} is predicted to converge on the downstream approach at {final_approach_fix}",
                        "other_aircraft": other.callsign,
                        "conflict_waypoint": final_approach_fix,
                        "predicted_separation_nm": round(
                            abs(own_eta - other_eta) * max(float(own_speed), float(other_speed)) / 3600.0, 2
                        )}
        return {"safe": True, "reason": "Downstream route clear and required separation available"}

    def _can_approach_handoff(self, aircraft, from_fallback=False):
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

        # APP38 and APP32 are separate entry choices. Score both against
        # current ownership, route reachability, altitude readiness and the
        # predicted spacing at every downstream common approach fix.
        points = self.approach_ai.approach_points
        entries = [(index, point) for index, point in enumerate(points)
                   if str(point.get("id", "")).upper() in {"APP32", "APP38"}]
        if not entries:
            self._release_approach_reservation(aircraft)
            return {"safe": False, "reason": "No configured APP32/APP38 entry is available"}

        prior_entry = next((node for node, owner in self.approach_reservations.items()
                            if owner is aircraft), None)
        candidates = []
        failures = []
        for index, point in entries:
            node = str(point["id"]).upper()
            owner = self.approach_reservations.get(node)
            if owner is not None and owner is not aircraft:
                failures.append(f"{node} reserved for {owner.callsign}")
                continue
            eta = self._approach_entry_eta_seconds(aircraft, route, own_progress,
                                                   float(own_speed), point,
                                                   from_fallback=from_fallback)
            if eta is None:
                failures.append(f"{node} trajectory cannot be predicted")
                continue
            speed = float(own_speed)
            predicted_altitude = float(aircraft.altitude_ft) + float(
                getattr(aircraft, "vertical_speed_fpm", 0) or 0
            ) * eta / 60.0
            target_altitude = float(point.get("altitude_ft", predicted_altitude))
            required_vs = (target_altitude - float(aircraft.altitude_ft)) * 60.0 / max(eta, 1.0)
            if abs(required_vs) > 1500.0:
                failures.append(f"{node} altitude is not reachable at current readiness")
                continue

            # Routes must end close enough to join the selected entry through
            # the configured approach geometry.
            endpoint = route.get("waypoints", [])[-1]
            connector_nm = self._route_segment_length_nm(
                endpoint, {"latitude": point["latitude"], "longitude": point["longitude"]}
            )
            if connector_nm > self.SEQUENCING_WINDOW_NM:
                failures.append(f"{node} is incompatible with this arrival route")
                continue

            occupied = next((other for other in traffic
                             if other is not aircraft
                             and getattr(other, "phase", "") in {"ARRIVAL", "FINAL"}
                             and self._route_segment_length_nm(
                                 {"latitude": point["latitude"], "longitude": point["longitude"]},
                                 {"latitude": other.lat, "longitude": other.lon},
                             ) < self.MIN_MERGE_SEPARATION_NM
                             and abs(predicted_altitude - float(other.altitude_ft))
                             < self.MIN_VERTICAL_SEPARATION_FT), None)
            if occupied is not None:
                failures.append(f"{node} is currently occupied by {occupied.callsign}")
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
                    if abs(own_alt - other_alt) < self.MIN_VERTICAL_SEPARATION_FT:
                        minimum_margin = min(minimum_margin, gap - self.MIN_MERGE_SEPARATION_NM)
                        if gap < self.MIN_MERGE_SEPARATION_NM:
                            if own_eta > other_eta:
                                desired_eta = other_eta + self.MIN_MERGE_SEPARATION_NM * 3600.0 / max(
                                    speed, float(other_speed)
                                )
                                required_speed = own_eta * speed / max(desired_eta, 1.0)
                                minimum_speed = self._minimum_sequencing_speed(aircraft)
                                if minimum_speed <= required_speed < speed - 1.0:
                                    speed_solution = (required_speed if speed_solution is None
                                                      else min(speed_solution, required_speed))
                                    continue
                            unsafe_reason = (
                                f"{other.callsign} is predicted inside 5 NM at "
                                f"{points[downstream_index].get('id', 'approach fix')}"
                            )
                            break
                if unsafe_reason:
                    break
            if unsafe_reason:
                failures.append(f"{node}: {unsafe_reason}")
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
                        if (abs(own_alt - other_alt) < self.MIN_VERTICAL_SEPARATION_FT
                                and gap < self.MIN_MERGE_SEPARATION_NM):
                            unsafe_reason = (
                                f"{other.callsign} remains inside 5 NM at "
                                f"{points[downstream_index].get('id', 'approach fix')} after speed adjustment"
                            )
                            break
                        if abs(own_alt - other_alt) < self.MIN_VERTICAL_SEPARATION_FT:
                            adjusted_margin = min(adjusted_margin, gap - self.MIN_MERGE_SEPARATION_NM)
                    if unsafe_reason:
                        break
                if unsafe_reason:
                    failures.append(f"{node}: {unsafe_reason}")
                    continue
                minimum_margin = adjusted_margin
            # Preserve a valid prior choice to avoid fallback oscillation;
            # otherwise maximize separation margin, then choose the closer
            # connector for this route.
            candidates.append((node == prior_entry, minimum_margin, -connector_nm,
                               eta, index, node, speed_solution))

        if not candidates:
            self._release_approach_reservation(aircraft)
            return {"safe": False,
                    "reason": "; ".join(failures) or "Neither approach entry is safe"}
        candidates.sort(reverse=True)
        _, _, _, eta, selected_index, selected_node, speed_solution = candidates[0]
        self._release_approach_reservation(aircraft)
        self.approach_reservations[selected_node] = aircraft
        aircraft._approach_entry_index = selected_index
        aircraft._approach_entry_node = selected_node
        selected_eta = (eta * float(own_speed) / speed_solution
                        if speed_solution is not None else eta)
        return {"safe": True, "reason": f"{selected_node} selected with downstream separation available",
                "approach_entry_index": selected_index, "approach_entry_node": selected_node,
                "predicted_entry_eta_seconds": round(selected_eta, 1),
                **({"speed_reduction_kts": round(speed_solution, 1)}
                   if speed_solution is not None else {})}

    def _approach_entry_eta_seconds(self, aircraft, route, progress, speed, point,
                                    from_fallback=False):
        route_points = route.get("waypoints", [])
        if not route_points or speed <= 0:
            return None
        if from_fallback:
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
        active = {id(item): item for item in self.manager.all()}
        for node, owner in list(self.approach_reservations.items()):
            if id(owner) not in active or active[id(owner)] is not owner:
                self._release_approach_reservation(owner)
                continue
            entry_index = getattr(owner, "_approach_entry_index", None)
            if (getattr(owner, "phase", "") not in {"ARRIVAL", "FINAL"}
                    or (isinstance(entry_index, int)
                        and isinstance(getattr(owner, "approach_index", None), int)
                        and owner.approach_index > entry_index)):
                self._release_approach_reservation(owner)

    def _release_approach_reservation(self, aircraft):
        for node, owner in list(self.approach_reservations.items()):
            if owner is aircraft:
                self.approach_reservations.pop(node, None)
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
                continue
            points = route["waypoints"]
            if not points:
                continue
            first = points[0]
            aircraft = spawn_aircraft(callsign, aircraft_data["aircraft_type"], first["latitude"], first["longitude"])
            aircraft.phase = "DEPARTURE"
            initial_altitude = first.get("altitude", {}).get("feet")
            if isinstance(initial_altitude, (int, float)):
                aircraft.altitude_ft = int(initial_altitude)
                aircraft.target_altitude_ft = int(initial_altitude)
            aircraft.destination = aircraft_data["destination"]
            aircraft.assign_route([p["name"] for p in points])
            aircraft.departure_waypoints = points
            aircraft.departure_route_id = route["route_id"]
            self.scheduler.db.mark_departing(callsign)
            self.manager.add(aircraft)
            print(f"DEPARTURE SPAWNED: {callsign} route {route['route_id']}")

    def _departure_route_for(self, destination):
        routes = list(self.departure_routes.values())
        if not routes:
            return None
        destination = str(destination or "").upper()
        matching = [r for r in routes if destination in r.get("name", "").upper()
                    or destination == r["waypoints"][-1]["name"].upper()]
        if matching:
            return min(matching, key=lambda route: route["route_id"])
        return routes[sum(destination.encode("utf-8")) % len(routes)]

    def update(
        self,
        dt=1.0
    ):

        self.spawn_due_arrivals(dt)

        self.process_due_departures()

        traffic = self.manager.all()
        self._restore_shared_waypoint_speeds(traffic)
        self._detect_shared_waypoint_conflicts(traffic)
        self._enforce_predictive_separation(traffic)
        apply_arrival_separation(traffic, self.graph)
        self._record_route_following_actions(traffic)
        self._apply_shared_waypoint_separation(traffic)

        for aircraft in traffic:
            try:
                if aircraft.phase == "DEPARTURE":
                    self.guidance.update(aircraft)
                    if aircraft.target_node is None:
                        aircraft.lat = self.departure_waypoints[aircraft.assigned_node]["latitude"]
                        aircraft.lon = self.departure_waypoints[aircraft.assigned_node]["longitude"]
                        self.manager.remove(aircraft)
                        self.scheduler.aircraft_completed(aircraft.callsign)
                        print(f"DEPARTURE COMPLETED: {aircraft.callsign} | NEXT ARRIVAL SCHEDULED")
                        continue
                    waypoint = next((p for p in aircraft.departure_waypoints if p["name"] == aircraft.target_node), None)
                    if waypoint:
                        altitude = waypoint.get("altitude", {})
                        if altitude.get("feet") is not None:
                            aircraft.assign_altitude(altitude["feet"])
                        speed = waypoint.get("speed", {})
                        if speed.get("knots") is not None:
                            aircraft.assign_speed(speed["knots"])
                    continue

                # Pending asynchronous decisions leave the current clearance in
                # place; movement still happens in the common pass below.
                if not mistral_controller.has_pending_conflict_for(aircraft.callsign):
                    self.arrival_ai.update(aircraft)
                    self.guidance.update(aircraft)

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
