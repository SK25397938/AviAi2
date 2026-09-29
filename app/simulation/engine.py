import math

from app.simulation.manager import AircraftManager
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
            self.graph
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

        initial_batch = not self.traffic_manager.initial_arrivals_spawned
        arrivals = self.traffic_manager.update(
            dt,
            can_release_arrival=self._arrival_has_spacing
        )

        active_callsigns = {
            aircraft.callsign
            for aircraft in self.manager.all()
        }

        if not self.arrival_routes:
            return

        for index, aircraft_data in enumerate(arrivals):

            callsign = aircraft_data["callsign"]

            if callsign in active_callsigns:
                continue

            route_id = self._arrival_route_for(callsign)

            if route_id is None:
                continue

            try:

                aircraft = self.spawn(
                    aircraft_type=aircraft_data["aircraft_type"],
                    callsign=callsign,
                    route_id=route_id
                )

                aircraft.arrival_route_id = route_id
                if initial_batch:
                    route = self.arrival_routes[route_id]
                    distance_nm = 0.0
                    route_length_nm = sum(
                        self._route_segment_length_nm(start, end)
                        for start, end in zip(
                            route.get("waypoints", []),
                            route.get("waypoints", [])[1:]
                        )
                    )
                    if route_length_nm >= distance_nm:
                        self._position_on_arrival_route(
                            aircraft,
                            route,
                            distance_nm
                        )

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

    def _arrival_route_for(self, callsign):

        route_ids = list(self.arrival_routes)
        if not route_ids:
            return None

        route_loads = {
            route_id: sum(
                1
                for aircraft in self.manager.all()
                if getattr(aircraft, "arrival_route_id", None) == route_id
            )
            for route_id in route_ids
        }
        return min(route_ids, key=lambda route_id: route_loads[route_id])

    def _arrival_has_spacing(self, candidate):

        route_id = self._arrival_route_for(candidate["callsign"])
        if route_id is None:
            return True

        route = self.arrival_routes[route_id]
        for aircraft in self.manager.all():
            if getattr(aircraft, "arrival_route_id", None) != route_id:
                continue

            progress = self._route_progress_nm(aircraft, route)
            if progress is not None and progress < 4.5:
                return False

        return True

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

                try:

                    mistral_controller.update(
                        aircraft,
                        traffic
                    )

                except Exception as error:

                    if "429" not in str(error):

                        print(
                            f"MISTRAL ERROR "
                            f"{aircraft.callsign}: {error}"
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

                move_aircraft(
                    aircraft,
                    dt
                )

            except Exception as error:

                print(
                    f"AIRCRAFT UPDATE ERROR "
                    f"{aircraft.callsign}: {error}"
                )
