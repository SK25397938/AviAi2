from app.simulation.manager import AircraftManager
from app.simulation.spawn import spawn_aircraft
from app.simulation.physics import move_aircraft
from app.simulation.aircraft_db import AircraftDatabase

from app.intelligence.arrival_manager import ArrivalManager
from app.intelligence.arrival_ai import ArrivalAI
from app.intelligence.guidance_ai import GuidanceAI
from app.intelligence.approach_ai import ApproachAI
from app.intelligence.landing_ai import LandingAI

from app.intelligence.mistral.controller import mistral_controller

from app.navigation.runway import Runway
from app.navigation.centerline import build_centerline
from app.navigation.grid_builder import build_grid
from app.navigation.graph_builder import build_graph
from app.navigation.loader import load_arrival_routes


class SimulationEngine:

    def __init__(
        self,
        airport_lat=19.0887,
        airport_lon=72.8679
    ):

        self.manager = AircraftManager()

        self.aircraft_db = AircraftDatabase()

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

        self.prototype_routes = {
            "AIQ432": "001",
            "SIA421": "002",
            "UAE502": "003",
            "DLH757": "004"
        }

    def spawn(
        self,
        aircraft_type,
        callsign
    ):

        route_id = self.prototype_routes.get(callsign)

        if route_id is None:

            raise ValueError(
                f"No prototype arrival route assigned to {callsign}"
            )

        route_definition = self.arrival_routes[route_id]
        waypoints = route_definition["waypoints"]
        first_waypoint = waypoints[0]

        aircraft = spawn_aircraft(
            callsign,
            aircraft_type,
            first_waypoint["latitude"],
            first_waypoint["longitude"]
        )

        aircraft.assign_route(
            [waypoint["name"] for waypoint in waypoints]
        )

        print(
            f"ROUTE ASSIGNED: {callsign} -> {route_id} "
            f"at {first_waypoint['name']}"
        )

        self.manager.add(
            aircraft
        )

        return aircraft

    def update(
        self,
        dt=1.0
    ):

        traffic = self.manager.all()

        for aircraft in traffic:

            try:

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

                    print(
                        f"FINAL APPROACH COMPLETE: "
                        f"{aircraft.callsign} -> "
                        f"RWY 27 / 23ft"
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

                move_aircraft(
                    aircraft,
                    dt
                )

            except Exception as error:

                print(
                    f"AIRCRAFT UPDATE ERROR "
                    f"{aircraft.callsign}: {error}"
                )