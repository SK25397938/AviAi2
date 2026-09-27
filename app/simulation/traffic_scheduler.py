import random
import time

from app.simulation.traffic_db import TrafficDatabase


class TrafficScheduler:

    MIN_ARRIVAL_GAP = 35
    MAX_ARRIVAL_GAP = 70

    MIN_DEPARTURE_GAP = 60
    MAX_DEPARTURE_GAP = 150

    MIN_TURNAROUND = 2700
    MAX_TURNAROUND = 7200

    def __init__(self):

        self.db = TrafficDatabase()

        self.random = random.Random()

        self.next_departure_release = None

        self.initialize_schedule()

    def initialize_schedule(self):

        now = time.time()

        airport_aircraft = self.db.get_airport_aircraft()

        unscheduled_departures = [
            aircraft
            for aircraft in airport_aircraft
            if aircraft["departure_time"] is None
        ]

        self.random.shuffle(
            unscheduled_departures
        )

        current_time = now

        for index, aircraft in enumerate(
            unscheduled_departures
        ):

            if index == 0:

                departure_time = now

            else:

                current_time += self.random.randint(
                    self.MIN_DEPARTURE_GAP,
                    self.MAX_DEPARTURE_GAP
                )

                departure_time = current_time

            self.db.set_departure_time(
                aircraft["callsign"],
                departure_time
            )

        self.next_departure_release = now

    def get_due_departures(self):

        now = time.time()

        if now < self.next_departure_release:

            return []

        departures = self.db.get_available_departures(
            now
        )

        if not departures:

            return []

        departures.sort(
            key=lambda aircraft: (
                aircraft["departure_time"]
                if aircraft["departure_time"] is not None
                else 0
            )
        )

        aircraft = departures[0]

        self.next_departure_release = (
            now
            + self.random.randint(
                self.MIN_DEPARTURE_GAP,
                self.MAX_DEPARTURE_GAP
            )
        )

        return [aircraft]

    def aircraft_landed(
        self,
        callsign
    ):

        now = time.time()

        turnaround = self.random.randint(
            self.MIN_TURNAROUND,
            self.MAX_TURNAROUND
        )

        next_departure = now + turnaround

        self.db.mark_landed(
            callsign,
            next_departure
        )

    def aircraft_completed(
        self,
        callsign
    ):

        now = time.time()

        next_arrival = (
            now
            + self.random.randint(
                self.MIN_ARRIVAL_GAP,
                self.MAX_ARRIVAL_GAP
            )
        )

        self.db.set_status(
            callsign,
            "COMPLETED"
        )

        self.db.set_arrival_time(
            callsign,
            next_arrival
        )

    def update(self):

        return {"departures": self.get_due_departures()}
