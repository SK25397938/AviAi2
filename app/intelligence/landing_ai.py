class LandingAI:

    ROLLOUT_SPEED = 30
    ROLLOUT_TIME = 5

    def __init__(
        self,
        runway,
        graph
    ):
        self.runway = runway
        self.graph = graph
        self.rollout_timers = {}

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

        self.rollout_timers[
            aircraft.callsign
        ] = 0

        print(
            f"TOUCHDOWN: "
            f"{aircraft.callsign}"
        )

    def _rollout(
        self,
        aircraft
    ):
        callsign = aircraft.callsign

        aircraft.on_ground = True

        aircraft.assign_heading(
            self.runway.centerline_heading()
        )

        aircraft.assign_speed(
            max(
                self.ROLLOUT_SPEED,
                aircraft.speed_kts - 4
            )
        )

        aircraft.assign_altitude(
            0
        )

        self.rollout_timers[callsign] = (
            self.rollout_timers.get(
                callsign,
                0
            ) + 1
        )

        if self.rollout_timers[callsign] < self.ROLLOUT_TIME:
            return

        aircraft.runway_vacated = True
        aircraft.landing_complete = True

        aircraft.phase = "GROUND"
        aircraft.state = "TAXI"

        aircraft.approach_phase = "RUNWAY_EXIT"

        aircraft.assigned_runway_exit = None
        aircraft.exit_distance_km = None

        aircraft.target_node = None
        aircraft.assigned_node = None

        aircraft.previous_taxi_node = None

        aircraft.assign_speed(
            15
        )

        del self.rollout_timers[callsign]

        print(
            f"LANDING COMPLETE: "
            f"{aircraft.callsign} -> GROUND/TAXI"
        )