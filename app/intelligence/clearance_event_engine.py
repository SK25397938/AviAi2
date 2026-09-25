class ClearanceEventEngine:

    HEADING_TOLERANCE = 3

    ALTITUDE_TOLERANCE = 100

    SPEED_TOLERANCE = 5

    def update(
        self,
        aircraft
    ):

        if aircraft.ai_busy:

            return None

        if aircraft.phase == "FINAL":

            return None

        if aircraft.active_clearance.completed:

            if aircraft.pending_event == "":

                return None

        if aircraft.active_clearance.completed:

            return aircraft.pending_event

        if aircraft.phase == "FINAL":

            if aircraft.approach_phase == "LOCALIZER":

                aircraft.complete_clearance()

                aircraft.pending_event = "LOCALIZER_CAPTURED"

                return aircraft.pending_event

            if aircraft.approach_phase == "GLIDESLOPE":

                aircraft.complete_clearance()

                aircraft.pending_event = "GLIDESLOPE_CAPTURED"

                return aircraft.pending_event

            if aircraft.approach_phase == "FINAL_APPROACH":

                aircraft.complete_clearance()

                aircraft.pending_event = "LANDING_CLEARANCE"

                return aircraft.pending_event

        if aircraft.on_ground:

            if not aircraft.runway_vacated:

                aircraft.complete_clearance()

                aircraft.pending_event = "RUNWAY_EXIT"

                return aircraft.pending_event

            if aircraft.runway_vacated:

                aircraft.complete_clearance()

                aircraft.pending_event = "TAXI"

                return aircraft.pending_event

            if aircraft.parking_stand is not None:

                aircraft.complete_clearance()

                aircraft.pending_event = "PARKED"

                return aircraft.pending_event

        return None


clearance_event_engine = ClearanceEventEngine()
