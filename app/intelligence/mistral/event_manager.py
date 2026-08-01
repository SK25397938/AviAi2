from datetime import datetime
from datetime import timedelta


class AIEventManager:

    EVENT_INTERVAL = timedelta(

        seconds=15

    )

    def __init__(self):

        self.last_request = {}

    def should_request(

        self,

        aircraft,

        event

    ):

        now = datetime.utcnow()

        key = aircraft.callsign

        if key not in self.last_request:

            self.last_request[key] = {

                "time": now,

                "event": event

            }

            return True

        last = self.last_request[key]

        if last["event"] != event:

            self.last_request[key] = {

                "time": now,

                "event": event

            }

            return True

        if now - last["time"] >= self.EVENT_INTERVAL:

            self.last_request[key] = {

                "time": now,

                "event": event

            }

            return True

        return False

    def determine_event(

        self,

        aircraft

    ):

        if aircraft.on_ground:

            if aircraft.landing_complete:

                return "RUNWAY_EXIT"

            return "ROLLOUT"

        if aircraft.phase == "ARRIVAL":

            return "ARRIVAL"

        if aircraft.phase == "INITIAL_APPROACH":

            return "INITIAL_APPROACH"

        if aircraft.phase == "INTERMEDIATE":

            return "INTERMEDIATE"

        if aircraft.approach_phase == "INTERCEPT":

            return "INTERCEPT"

        if aircraft.approach_phase == "LOCALIZER":

            return "LOCALIZER"

        if aircraft.approach_phase == "GLIDESLOPE":

            return "GLIDESLOPE"

        if aircraft.approach_phase == "FINAL_APPROACH":

            return "FINAL_APPROACH"

        return "UPDATE"


event_manager = AIEventManager()