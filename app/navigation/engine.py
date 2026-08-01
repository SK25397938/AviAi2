from .loader import NavigationLoader
from .graph import NavigationGraph
from .procedure import ProcedureManager


class NavigationEngine:

    def __init__(self, airport):

        loader = NavigationLoader(airport)

        self.airport = airport.upper()

        self.waypoints = loader.load("waypoints.json")
        self.stars = loader.load("procedures.json")
        self.sids = loader.load("departures.json")
        self.approaches = loader.load("approaches.json")
        self.holds = loader.load("holdings.json")
        self.airspace = loader.load("airspace.json")

        self.graph = NavigationGraph()

        self.manager = ProcedureManager(
            self.stars,
            self.sids,
            self.approaches,
            self.holds
        )

        self.waypoints_by_name = {
            wp["name"].upper(): wp
            for wp in self.waypoints["waypoints"]
        }

        self.build_graph()

    def build_graph(self):

        collections = [
            self.stars["stars"],
            self.sids["sids"],
            self.approaches["approaches"]
        ]

        for collection in collections:

            for procedure in collection:

                route = procedure["route"]

                for i in range(len(route) - 1):

                    self.graph.add_edge(
                        route[i]["fix"],
                        route[i + 1]["fix"]
                    )

    def get_waypoint(self, name):

        return self.waypoints_by_name.get(
            name.upper()
        )

    def get_star(self, name):

        return self.manager.get_star(name)

    def get_sid(self, name):

        return self.manager.get_sid(name)

    def get_approach(self, name):

        return self.manager.get_approach(name)

    def get_hold(self, name):

        return self.manager.get_hold(name)