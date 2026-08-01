from app.intelligence.instruction_manager import instruction_manager


class ApproachAI:

    def __init__(
        self,
        runway
    ):

        self.runway = runway

    def update(
        self,
        aircraft
    ):

        if aircraft.phase != "FINAL":

            return

        aircraft.state = "APPROACH"

        if aircraft.approach_phase == "INTERCEPT":

            self._intercept(
                aircraft
            )

        elif aircraft.approach_phase == "LOCALIZER":

            self._localizer(
                aircraft
            )

        elif aircraft.approach_phase == "GLIDESLOPE":

            self._glideslope(
                aircraft
            )

        elif aircraft.approach_phase == "FINAL_APPROACH":

            self._final(
                aircraft
            )

    def _intercept(
        self,
        aircraft
    ):

        cross = self.runway.cross_track_error(

            aircraft.lat,

            aircraft.lon

        )

        along = self.runway.along_track_distance(

            aircraft.lat,

            aircraft.lon

        )

        if aircraft.clearance.heading is None:

            intercept_heading = (

                self.runway.centerline_heading()

                +

                25

            ) % 360

            aircraft.assign_heading(

                intercept_heading

            )

        if aircraft.clearance.altitude_ft is None:

            aircraft.assign_altitude(

                3000

            )

        if aircraft.clearance.speed_kts is None:

            aircraft.assign_speed(

                180

            )

        if aircraft.last_instruction == "":

            instruction_manager.issue(

                aircraft,

                "Approach",

                "Intercept localizer."

            )

        if cross <= 0.30 and along <= 10:

            aircraft.approach_phase = "LOCALIZER"

    def _localizer(
        self,
        aircraft
    ):

        along = self.runway.along_track_distance(

            aircraft.lat,

            aircraft.lon

        )

        aircraft.assign_heading(

            self.runway.centerline_heading()

        )

        if aircraft.clearance.speed_kts is None:

            aircraft.assign_speed(

                170

            )

        if aircraft.last_instruction == "":

            instruction_manager.issue(

                aircraft,

                "Approach",

                "Localizer captured."

            )

        if along <= 6:

            aircraft.approach_phase = "GLIDESLOPE"

    def _glideslope(
        self,
        aircraft
    ):

        along = self.runway.along_track_distance(

            aircraft.lat,

            aircraft.lon

        )

        aircraft.assign_heading(

            self.runway.centerline_heading()

        )

        aircraft.assign_altitude(

            self.runway.glidepath_altitude(

                along

            )

        )

        if aircraft.clearance.speed_kts is None:

            aircraft.assign_speed(

                150

            )

        if aircraft.last_instruction == "":

            instruction_manager.issue(

                aircraft,

                "Approach",

                "Glideslope captured."

            )

        if along <= 2:

            aircraft.approach_phase = "FINAL_APPROACH"

    def _final(
        self,
        aircraft
    ):

        along = self.runway.along_track_distance(

            aircraft.lat,

            aircraft.lon

        )

        aircraft.assign_heading(

            self.runway.centerline_heading()

        )

        aircraft.assign_altitude(

            self.runway.glidepath_altitude(

                along

            )

        )

        if aircraft.clearance.speed_kts is None:

            aircraft.assign_speed(

                145 if along > 1 else 135

            )

        aircraft.assign_runway(

            self.runway.ident

        )

        if aircraft.last_instruction == "":

            instruction_manager.issue(

                aircraft,

                "Tower",

                f"Cleared to land Runway {self.runway.ident}."

            )