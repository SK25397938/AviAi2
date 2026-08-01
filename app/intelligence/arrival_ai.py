from app.intelligence.descent_manager import DescentManager
from app.intelligence.speed_manager import SpeedManager
from app.intelligence.instruction_manager import instruction_manager


class ArrivalAI:

    def __init__(self):

        self.descent = DescentManager()

        self.speed = SpeedManager()

    def determine_phase(
        self,
        distance_nm
    ):

        if distance_nm > 20:

            return "ARRIVAL"

        if distance_nm > 12:

            return "INITIAL_APPROACH"

        if distance_nm > 5:

            return "INTERMEDIATE"

        return "FINAL"

    def update(
        self,
        aircraft
    ):

        if not aircraft.route:

            return

        remaining = (

            len(

                aircraft.route

            )

            -

            aircraft.route_index

        )

        distance_nm = remaining

        aircraft.phase = self.determine_phase(

            distance_nm

        )

        aircraft.recommended_altitude = (

            self.descent.target_altitude(

                distance_nm

            )

        )

        aircraft.recommended_speed = (

            self.speed.target_speed(

                distance_nm

            )

        )

        if not hasattr(

            aircraft,

            "last_instruction"

        ):

            aircraft.last_instruction = ""

        if aircraft.last_instruction == "":

            instruction_manager.issue(

                aircraft,

                "Arrival",

                "Awaiting AI Arrival Clearance."

            )