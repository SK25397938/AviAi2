import random
import time

from app.simulation.traffic_db import TrafficDatabase


class TrafficScheduler:

    MIN_ARRIVAL_GAP = 180
    MAX_ARRIVAL_GAP = 720

    MIN_DEPARTURE_GAP = 240
    MAX_DEPARTURE_GAP = 900

    MIN_TURNAROUND = 2700
    MAX_TURNAROUND = 7200

    def __init__(self):

        self.db = TrafficDatabase()

        self.random = random.Random()

        self.initialize_schedule()

    def initialize_schedule(self):

        now = time.time()

        arrivals = self.db.get_by_status(
            "ARRIVAL"
        )

        unscheduled_arrivals = [
            aircraft
            for aircraft in arrivals
            if aircraft["arrival_time"] is None
        ]

        if unscheduled_arrivals:

            current_time = now

            self.random.shuffle(
                unscheduled_arrivals
            )

            for aircraft in unscheduled_arrivals:

                current_time += self.random.randint(
                    self.MIN_ARRIVAL_GAP,
                    self.MAX_ARRIVAL_GAP
                )

                self.db.set_arrival_time(
                    aircraft["callsign"],
                    current_time
                )

        airport_aircraft = self.db.get_airport_aircraft()

        unscheduled_departures = [
            aircraft
            for aircraft in airport_aircraft
            if aircraft["departure_time"] is None
        ]

        if unscheduled_departures:

            current_time = now

            self.random.shuffle(
                unscheduled_departures
            )

            for aircraft in unscheduled_departures:

                current_time += self.random.randint(
                    self.MIN_DEPARTURE_GAP,
                    self.MAX_DEPARTURE_GAP
                )

                self.db.set_departure_time(
                    aircraft["callsign"],
                    current_time
                )

    def get_due_arrivals(self):

        return self.db.get_available_arrivals(
            time.time()
        )

    def get_due_departures(self):

        return self.db.get_available_departures(
            time.time()
        )

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

        next_arrival = now + self.random.randint(
            self.MIN_ARRIVAL_GAP,
            self.MAX_ARRIVAL_GAP
        )

        self.db.return_to_arrival(
            callsign,
            next_arrival
        )

    def update(self):

        return {
            "arrivals": self.get_due_arrivals(),
            "departures": self.get_due_departures()
        }