from app.intelligence.controller import ApproachController


class Simulator:

    def __init__(self, graph):

        self.graph = graph

        self.controller = ApproachController(graph)

        self.aircraft = []

        self.time = 0

    def add_aircraft(self, aircraft):

        self.aircraft.append(aircraft)

    def step(self):

        self.time += 1

        for aircraft in self.aircraft:

            result = self.controller.plan(aircraft)

            aircraft.assign_node(result["start"])

            if len(result["path"]) > 1:

                aircraft.assign_target(result["path"][1])

                aircraft.assign_heading(

                    result["instructions"][0]["heading"]

                )

    def run(self, steps):

        for _ in range(steps):

            self.step()