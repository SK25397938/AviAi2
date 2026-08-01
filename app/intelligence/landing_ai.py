class LandingAI:

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

        if aircraft.approach_phase == "FINAL_APPROACH":

            self._check_flare(
                aircraft
            )

        elif aircraft.approach_phase == "FLARE":

            self._flare(
                aircraft
            )

        elif aircraft.approach_phase == "TOUCHDOWN":

            self._touchdown(
                aircraft
            )

        elif aircraft.approach_phase == "ROLLOUT":

            self._rollout(
                aircraft
            )

    def _check_flare(
        self,
        aircraft
    ):

        distance = self.runway.distance_to_touchdown(
            aircraft.lat,
            aircraft.lon
        )

        if distance <= 0.25:

            aircraft.approach_phase = "FLARE"

    def _flare(
        self,
        aircraft
    ):

        aircraft.assign_heading(
            self.runway.centerline_heading()
        )

        aircraft.assign_speed(
            135
        )

        aircraft.assign_altitude(
            10
        )

        if aircraft.altitude_ft <= 10:

            aircraft.approach_phase = "TOUCHDOWN"

    def _touchdown(
        self,
        aircraft
    ):

        aircraft.on_ground = True

        aircraft.assign_heading(
            self.runway.centerline_heading()
        )

        aircraft.assign_speed(
            120
        )

        aircraft.assign_altitude(
            0
        )

        aircraft.approach_phase = "ROLLOUT"

    def _rollout(
        self,
        aircraft
    ):

        aircraft.assign_heading(
            self.runway.centerline_heading()
        )

        if aircraft.speed_kts > 80:

            aircraft.assign_speed(
                aircraft.speed_kts - 4
            )

        elif aircraft.speed_kts > 40:

            aircraft.assign_speed(
                aircraft.speed_kts - 2
            )

        else:

            aircraft.assign_speed(
                20
            )

            aircraft.phase = "GROUND"

            aircraft.state = "TAXI"

            aircraft.landing_complete = True