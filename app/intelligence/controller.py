from .locator import nearest_node
from .planner import shortest_path
from .vectoring import next_heading


class ApproachController:

    def __init__(
        self,
        graph
    ):

        self.graph = graph

    def plan(
        self,
        aircraft
    ):

        start = nearest_node(

            self.graph,

            aircraft.lat,

            aircraft.lon

        )

        path = shortest_path(

            self.graph,

            start,

            "N1"

        )

        instructions = []

        for i in range(

            len(path) - 1

        ):

            heading = next_heading(

                self.graph,

                path[i],

                path[i + 1]

            )

            instructions.append({

                "from": path[i],

                "to": path[i + 1],

                "heading": round(heading),

                "altitude": aircraft.target_altitude_ft,

                "speed": aircraft.target_speed_kts,

                "instruction":

                    f"Proceed {path[i]} → {path[i+1]}"

            })

        return {

            "start": start,

            "path": path,

            "next_node":

                path[1] if len(path) > 1 else None,

            "remaining":

                max(

                    len(path) - 1,

                    0

                ),

            "instructions": instructions

        }