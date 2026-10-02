import json
import math
from pathlib import Path
from datetime import datetime

from app.simulation.manager import AircraftManager
from app.simulation.holding_manager import HoldingManager
from app.simulation.spawn import spawn_aircraft
from app.simulation.physics import move_aircraft
from app.simulation.aircraft_db import AircraftDatabase
from app.simulation.traffic_manager import TrafficManager
from app.simulation.traffic_scheduler import TrafficScheduler

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
from app.navigation.loader import load_arrival_routes, load_departure_routes


class SimulationEngine:

    ARRIVAL_ROUTE_CAPACITIES = {
        "001": 5,
        "002": 4,
        "003": 4,
        "004": 5
    }

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
        self.departure_routes = load_departure_routes()
        self.departure_waypoints = {}

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
        self.ai_decision_log = []

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

        self.arrival = ArrivalManager(
            self.graph
        )

        self.arrival_ai = ArrivalAI()

        self.guidance = GuidanceAI(
            self.graph,
            self.holding_manager,
            self.arrival_routes
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

        aircraft.assign_route(
            [
                waypoint["name"]
                for waypoint in waypoints
            ]
        )

        print(
            f"ROUTE ASSIGNED: {callsign} -> {route_id} "
            f"at {first_waypoint['name']}"
        )

        self.manager.add(
            aircraft
        )

        return aircraft

    def spawn_due_arrivals(self, dt=1.0):

        arrivals = self.traffic_manager.update(
            dt,
            can_release_arrival=self._arrival_has_spacing
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

    def _position_on_arrival_route(self, aircraft, route, distance_nm):

        if not isinstance(route, dict):
            return

        points = route.get("waypoints", [])
        if len(points) < 2:
            return

        remaining = max(0.0, distance_nm)
        for index, (start, end) in enumerate(zip(points, points[1:])):
            segment_nm = self._route_segment_length_nm(start, end)
            if remaining <= segment_nm or index == len(points) - 2:
                fraction = 0.0 if segment_nm == 0 else min(1.0, remaining / segment_nm)
                aircraft.lat = start["latitude"] + fraction * (
                    end["latitude"] - start["latitude"]
                )
                aircraft.lon = start["longitude"] + fraction * (
                    end["longitude"] - start["longitude"]
                )
                aircraft.route_index = index
                aircraft.assigned_node = aircraft.route[index]
                aircraft.target_node = aircraft.route[index + 1]
                return
            remaining -= segment_nm

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

        configured_routes = [
            route_id
            for route_id in route_ids
            if route_id in self.ARRIVAL_ROUTE_CAPACITIES
        ]
        eligible_routes = [
            route_id
            for route_id in configured_routes
            if route_loads[route_id] < self.ARRIVAL_ROUTE_CAPACITIES[route_id]
        ]
        if eligible_routes:
            return min(eligible_routes, key=lambda route_id: route_loads[route_id])

        if configured_routes:
            return None

        return min(route_ids, key=lambda route_id: route_loads[route_id])

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
            aircraft_route_id = getattr(aircraft, "arrival_route_id", None)
            if aircraft_route_id not in self.arrival_routes:
                continue

            aircraft_entry_key, _ = self._arrival_entry(aircraft_route_id)
            if aircraft_entry_key != entry_key:
                continue

            distance_nm = self._route_segment_length_nm(
                entry_point,
                {"latitude": aircraft.lat, "longitude": aircraft.lon}
            )
            if distance_nm < 4.5:
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

    def _detect_shared_waypoint_conflicts(self, aircraft_list):

        shared_waypoint_groups = self._shared_arrival_waypoint_groups()
        if not shared_waypoint_groups:
            active_holds = {
                aircraft.callsign for aircraft in aircraft_list
                if self.holding_manager.has_active_holding_route(aircraft)
            }
            mistral_controller.clear_conflicts(set(), active_holds)
            return

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
            if (
                progress is None
                or not isinstance(speed, (int, float))
                or not math.isfinite(speed)
                or speed <= 0
            ):
                continue

            arrivals.append((aircraft, route_id, progress, float(speed)))

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
                        first[1] == second[1]
                        or id(second[0]) in held_this_update
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
                            second_eta_seconds = second_remaining * 3600.0 / second[3]
                            fastest_speed = max(first[3], second[3])
                            arrival_gap_nm = (
                                abs(first_eta_seconds - second_eta_seconds)
                                * fastest_speed
                                / 3600.0
                            )
                            if arrival_gap_nm >= 4.5:
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
                            detected_conflicts.append({
                                "waypoint": delayed_waypoint,
                                "aircraft": (first[0], second[0]),
                                "routes": self.arrival_routes,
                                "holding_routes": self.holding_routes,
                                "active_runway": self.runway.ident,
                            })
                            held_this_update.update((id(first[0]), id(second[0])))
                            break

                        if id(first[0]) in held_this_update or id(second[0]) in held_this_update:
                            break

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
            mistral_controller.request_conflict(
                conflict,
                self.holding_manager,
                self._record_ai_conflict_decision,
            )

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
            "reason": decision["reason"],
        })
        del self.ai_decision_log[50:]

    def _restore_shared_waypoint_speeds(self, aircraft_list):

        for aircraft in aircraft_list:
            applied_target = getattr(
                aircraft,
                "_engine_shared_waypoint_applied_target_speed",
                None
            )
            if applied_target is None:
                continue

            if getattr(aircraft, "target_speed_kts", None) == applied_target:
                aircraft.target_speed_kts = aircraft._engine_shared_waypoint_original_target_speed

            del aircraft._engine_shared_waypoint_applied_target_speed
            del aircraft._engine_shared_waypoint_original_target_speed

    def _apply_shared_waypoint_separation(self, aircraft_list):

        shared_waypoint_groups = self._shared_arrival_waypoint_groups()
        if not shared_waypoint_groups:
            return

        arrivals = []
        for aircraft in aircraft_list:
            route_id = getattr(aircraft, "arrival_route_id", None)
            if (
                getattr(aircraft, "phase", "") not in ("ARRIVAL", "FINAL")
                or route_id not in self.arrival_routes
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

                    if first_remaining <= second_remaining:
                        leader, leader_remaining = first, first_remaining
                        follower, follower_remaining = second, second_remaining
                    else:
                        leader, leader_remaining = second, second_remaining
                        follower, follower_remaining = first, first_remaining

                    if leader_remaining < -4.5 or follower_remaining <= 0:
                        continue

                    gap = follower_remaining - leader_remaining
                    if gap >= 4.5 or follower[5] <= 140:
                        continue

                    adjusted = max(
                        140.0,
                        leader[4] - (4.5 - gap) * 12.0
                    )
                    current_target = desired_targets.get(id(follower[0]), follower[5])
                    desired_targets[id(follower[0])] = min(
                        current_target,
                        adjusted
                    )

        for aircraft, _, _, _, _, target in arrivals:
            adjusted = desired_targets.get(id(aircraft), target)
            if adjusted < target:
                aircraft._engine_shared_waypoint_original_target_speed = target
                aircraft.target_speed_kts = max(140.0, adjusted)
                aircraft._engine_shared_waypoint_applied_target_speed = aircraft.target_speed_kts

    def process_due_departures(self):

        departures = self.scheduler.get_due_departures()

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
                    move_aircraft(aircraft, dt)
                    continue

                self.arrival_ai.update(
                    aircraft
                )

                self.guidance.update(
                    aircraft
                )

                if aircraft.phase == "FINAL":

                    self.approach_ai.update(
                        aircraft
                    )

                if getattr(
                    aircraft,
                    "approach_complete",
                    False
                ):

                    self.aircraft_db.upsert(
                        callsign=aircraft.callsign,
                        aircraft_type=aircraft.aircraft_type,
                        runway_exit="27",
                        taxiway="23ft"
                    )

                    self.scheduler.aircraft_landed(
                        aircraft.callsign
                    )

                    print(
                        f"FINAL APPROACH COMPLETE: "
                        f"{aircraft.callsign} -> RWY 27 / 23ft"
                    )

                    print(
                        f"TRAFFIC STORED AT AIRPORT: "
                        f"{aircraft.callsign}"
                    )

                    self.manager.remove(
                        aircraft
                    )

                    print(
                        f"AIRCRAFT REMOVED FROM SIM: "
                        f"{aircraft.callsign}"
                    )

                    continue

                self.landing_ai.update(
                    aircraft
                )

                apply_arrival_separation(traffic, self.graph)
                self._apply_shared_waypoint_separation(traffic)
                for arrival_aircraft in traffic:
                    self.guidance.apply_route_constraints(arrival_aircraft)

                move_aircraft(
                    aircraft,
                    dt
                )

            except Exception as error:

                print(
                    f"AIRCRAFT UPDATE ERROR "
                    f"{aircraft.callsign}: {error}"
                )
