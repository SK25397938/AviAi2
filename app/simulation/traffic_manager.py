import random

from app.simulation.traffic_db import TrafficDatabase


class TrafficManager:

    ROUTES = [

        ("AIC", "Air India", "FRA", "A350-900"),
        ("AIC", "Air India", "LHR", "B787-9"),
        ("AIC", "Air India", "CDG", "B787-9"),
        ("AIC", "Air India", "DXB", "B777-300ER"),
        ("AIC", "Air India", "DOH", "B787-9"),
        ("AIC", "Air India", "JED", "B787-9"),
        ("AIC", "Air India", "RUH", "A320"),
        ("AIC", "Air India", "DMM", "A320"),
        ("AIC", "Air India", "MRU", "A350-900"),
        ("AIC", "Air India", "JFK", "B777-300ER"),
        ("AIC", "Air India", "EWR", "B777-300ER"),

        ("IGO", "IndiGo", "IST", "A321"),
        ("IGO", "IndiGo", "LHR", "A321"),
        ("IGO", "IndiGo", "AMS", "A321"),
        ("IGO", "IndiGo", "DXB", "A321"),
        ("IGO", "IndiGo", "DOH", "A321"),
        ("IGO", "IndiGo", "JED", "A321"),
        ("IGO", "IndiGo", "RUH", "A321"),
        ("IGO", "IndiGo", "MCT", "A321"),
        ("IGO", "IndiGo", "NBO", "A321"),

        ("UAE", "Emirates", "DXB", "A380"),
        ("UAE", "Emirates", "DXB", "B777-300ER"),
        ("UAE", "Emirates", "DXB", "B777-300ER"),
        ("UAE", "Emirates", "DXB", "A380"),

        ("QTR", "Qatar Airways", "DOH", "B777-300ER"),
        ("QTR", "Qatar Airways", "DOH", "A350-900"),
        ("QTR", "Qatar Airways", "DOH", "B777-300ER"),

        ("ETD", "Etihad Airways", "AUH", "B787-9"),
        ("ETD", "Etihad Airways", "AUH", "A350-1000"),
        ("ETD", "Etihad Airways", "AUH", "B787-9"),

        ("OMA", "Oman Air", "MCT", "B787-9"),
        ("OMA", "Oman Air", "MCT", "B737-8"),

        ("GFA", "Gulf Air", "BAH", "A321"),
        ("GFA", "Gulf Air", "BAH", "B787-9"),

        ("KAC", "Kuwait Airways", "KWI", "A330-800"),
        ("KAC", "Kuwait Airways", "KWI", "A320"),

        ("JZR", "Jazeera Airways", "KWI", "A320"),

        ("SVA", "Saudia", "JED", "B787-9"),
        ("SVA", "Saudia", "RUH", "A330-300"),

        ("FDB", "flydubai", "DXB", "B737-800"),
        ("FDB", "flydubai", "DXB", "B737-800"),

        ("AIZ", "Air India Express", "DXB", "B737-8"),
        ("AIZ", "Air India Express", "AUH", "B737-8"),
        ("AIZ", "Air India Express", "DOH", "B737-8"),

        ("THY", "Turkish Airlines", "IST", "B787-9"),
        ("THY", "Turkish Airlines", "IST", "A350-900"),
        ("THY", "Turkish Airlines", "IST", "B787-9"),

        ("DLH", "Lufthansa", "FRA", "A380"),
        ("DLH", "Lufthansa", "FRA", "A350-900"),
        ("DLH", "Lufthansa", "MUC", "A350-900"),
        ("DLH", "Lufthansa", "MUC", "A350-900"),

        ("BAW", "British Airways", "LHR", "B787-9"),
        ("BAW", "British Airways", "LHR", "B777-300ER"),
        ("BAW", "British Airways", "LHR", "B787-9"),

        ("VIR", "Virgin Atlantic", "LHR", "B787-9"),

        ("AFR", "Air France", "CDG", "B777-300ER"),
        ("AFR", "Air France", "CDG", "B777-300ER"),

        ("KLM", "KLM", "AMS", "B777-300ER"),
        ("KLM", "KLM", "AMS", "B787-10"),

        ("SWR", "SWISS", "ZRH", "A330-300"),
        ("SWR", "SWISS", "ZRH", "A330-300"),

        ("SAS", "SAS", "CPH", "A350-900"),

        ("AUA", "Austrian Airlines", "VIE", "B787-9"),

        ("IBE", "Iberia", "MAD", "A330-300"),

        ("ITA", "ITA Airways", "FCO", "A330-900"),

        ("FIN", "Finnair", "HEL", "A350-900"),

        ("ETH", "Ethiopian Airlines", "ADD", "B787-9"),
        ("ETH", "Ethiopian Airlines", "ADD", "A350-900"),

        ("KQA", "Kenya Airways", "NBO", "B787-8"),
        ("KQA", "Kenya Airways", "NBO", "B787-8"),

        ("UGA", "Uganda Airlines", "EBB", "A330-800"),

        ("MSR", "EgyptAir", "CAI", "B737-800"),
        ("MSR", "EgyptAir", "CAI", "B787-9"),

        ("THT", "Air Tanzania", "DAR", "A220-300"),

        ("MAU", "Air Mauritius", "MRU", "A350-900"),
        ("MAU", "Air Mauritius", "MRU", "A330-900"),

        ("AIC", "Air India", "MRU", "A350-900"),

        ("HY", "Uzbekistan Airways", "TAS", "B787-8"),

        ("KZR", "Air Astana", "ALA", "A321LR"),

        ("AIC", "Air India", "JFK", "B777-300ER"),
        ("AIC", "Air India", "EWR", "B777-300ER")
    ]

    TOTAL_AIRCRAFT = 100
    ARRIVAL_COUNT = 70
    AIRPORT_COUNT = 30

    INITIAL_ARRIVALS = 5
    MAX_ACTIVE_ARRIVALS = 10

    MIN_ARRIVAL_INTERVAL = 180
    MAX_ARRIVAL_INTERVAL = 600

    def __init__(self):

        self.db = TrafficDatabase()

        self.simulation_time = 0.0

        self.arrival_timer = 0.0

        self.next_arrival_interval = random.uniform(
            self.MIN_ARRIVAL_INTERVAL,
            self.MAX_ARRIVAL_INTERVAL
        )

        self.seed_test_aircraft()

        self.initial_arrivals_spawned = False

    def build_test_aircraft(self):

        aircraft = []

        for index, route in enumerate(
            self.ROUTES[:self.TOTAL_AIRCRAFT]
        ):

            code, airline, origin, aircraft_type = route

            callsign = f"{code}{100 + index}"

            aircraft.append({
                "callsign": callsign,
                "aircraft_type": aircraft_type,
                "airline": airline,
                "origin": origin,
                "destination": "VABB",
                "side": "WEST"
            })

        return aircraft

    def seed_test_aircraft(self):

        aircraft_list = self.build_test_aircraft()

        for index, aircraft in enumerate(aircraft_list):

            existing = self.db.get(
                aircraft["callsign"]
            )

            if existing is not None:
                continue

            if index < self.ARRIVAL_COUNT:

                status = "WAITING_ARRIVAL"

                origin = aircraft["origin"]

            else:

                status = "AIRPORT"

                origin = "VABB"

            self.db.upsert(
                callsign=aircraft["callsign"],
                aircraft_type=aircraft["aircraft_type"],
                airline=aircraft["airline"],
                origin=origin,
                destination="VABB",
                side=aircraft["side"],
                status=status
            )

    def get_arrivals(self):

        return self.db.get_available_arrivals()

    def get_waiting_arrivals(self):

        return self.db.get_by_status(
            "WAITING_ARRIVAL"
        )

    def get_departures(self):

        return self.db.get_departures()

    def get_airport_aircraft(self):

        return self.db.get_airport_aircraft()

    def get_active_arrivals(self):

        return self.db.get_by_status(
            "ARRIVAL"
        )

    def spawn_initial_arrivals(self):

        if self.initial_arrivals_spawned:
            return []

        waiting = self.get_waiting_arrivals()

        selected = waiting[
            :self.INITIAL_ARRIVALS
        ]

        spawned = []

        for aircraft in selected:

            self.db.set_status(
                aircraft["callsign"],
                "ARRIVAL"
            )

            spawned.append(
                aircraft
            )

        self.initial_arrivals_spawned = True

        return spawned

    def update(
        self,
        dt
    ):

        self.simulation_time += dt

        spawned = []

        if not self.initial_arrivals_spawned:

            spawned.extend(
                self.spawn_initial_arrivals()
            )

            return spawned

        active_arrivals = self.get_active_arrivals()

        if len(active_arrivals) >= self.MAX_ACTIVE_ARRIVALS:

            return spawned

        self.arrival_timer += dt

        if self.arrival_timer < self.next_arrival_interval:

            return spawned

        waiting = self.get_waiting_arrivals()

        if not waiting:

            self.arrival_timer = 0.0

            return spawned

        aircraft = random.choice(
            waiting
        )

        self.db.set_status(
            aircraft["callsign"],
            "ARRIVAL"
        )

        spawned.append(
            aircraft
        )

        self.arrival_timer = 0.0

        self.next_arrival_interval = random.uniform(
            self.MIN_ARRIVAL_INTERVAL,
            self.MAX_ARRIVAL_INTERVAL
        )

        return spawned

    def mark_landed(
        self,
        callsign
    ):

        self.db.mark_landed(
            callsign
        )

    def mark_departing(
        self,
        callsign
    ):

        self.db.mark_departing(
            callsign
        )

    def mark_completed(
        self,
        callsign
    ):

        self.db.mark_completed(
            callsign
        )

    def mark_arrival_available(
        self,
        callsign
    ):

        self.db.set_status(
            callsign,
            "WAITING_ARRIVAL"
        )

    def mark_parked(
        self,
        callsign
    ):

        self.db.set_status(
            callsign,
            "AIRPORT"
        )