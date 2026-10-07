import random
import time

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

    INITIAL_ARRIVALS = 1
    MAX_ACTIVE_AIRCRAFT = 12
    # Each phase is (name, duration, arrival-gap range, active-aircraft cap).
    TRAFFIC_WAVES = (
        ("LOW", 210.0, (70.0, 105.0), 4),
        ("MEDIUM", 180.0, (42.0, 72.0), 8),
        ("HIGH", 120.0, (28.0, 48.0), 12),
    )

    def __init__(self):

        self.db = TrafficDatabase()

        self.arrival_timer = 0.0
        self.wave_index = 0
        self.wave_timer = 0.0

        self.initial_arrivals_spawned = False
        self.initial_arrival_delay = random.uniform(2.0, 18.0)
        self.next_arrival_interval = self._next_arrival_interval()

        self.seed_test_aircraft()

    @property
    def active_wave(self):
        return self.TRAFFIC_WAVES[self.wave_index]

    @property
    def active_wave_capacity(self):
        return min(self.MAX_ACTIVE_AIRCRAFT, self.active_wave[3])

    def _next_arrival_interval(self):
        low, high = self.active_wave[2]
        return random.uniform(low, high)

    def _advance_wave(self, dt):
        self.wave_timer += max(0.0, float(dt))
        while self.wave_timer >= self.active_wave[1]:
            self.wave_timer -= self.active_wave[1]
            self.wave_index = (self.wave_index + 1) % len(self.TRAFFIC_WAVES)
            self.next_arrival_interval = self._next_arrival_interval()

    def build_test_aircraft(self):

        aircraft = []

        routes = self.ROUTES[:self.TOTAL_AIRCRAFT]

        for index, route in enumerate(routes):

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

        arrival_count = round(len(aircraft_list) * 0.70)

        for index, aircraft in enumerate(aircraft_list):

            existing = self.db.get(
                aircraft["callsign"]
            )

            if existing is not None:
                continue

            if index < arrival_count:

                status = "WAITING_ARRIVAL"
                origin = aircraft["origin"]
                destination = "VABB"

            else:

                status = "AIRPORT"
                origin = "VABB"
                destination = aircraft["origin"]

            self.db.upsert(
                callsign=aircraft["callsign"],
                aircraft_type=aircraft["aircraft_type"],
                airline=aircraft["airline"],
                origin=origin,
                destination=destination,
                side=aircraft["side"],
                status=status
            )

    def get_waiting_arrivals(self):

        return self.db.get_by_status(
            "WAITING_ARRIVAL"
        )

    def get_active_arrivals(self):

        return (
            self.db.get_by_status("ARRIVAL")
            + self.db.get_by_status("ACTIVE_ARRIVAL")
        )

    def get_airport_aircraft(self):

        return self.db.get_by_status(
            "AIRPORT"
        )

    def spawn_initial_arrivals(self, dt=1.0):

        if self.initial_arrivals_spawned:
            return []

        self.arrival_timer += max(0.0, float(dt))
        if self.arrival_timer < self.initial_arrival_delay:
            return []

        previous_arrivals = (
            self.db.get_by_status("ARRIVAL")
            + self.db.get_by_status("ACTIVE_ARRIVAL")
        )

        for aircraft in previous_arrivals:
            self.db.set_status(
                aircraft["callsign"],
                "WAITING_ARRIVAL"
            )

        waiting = self.get_waiting_arrivals()
        selected = random.sample(waiting, min(self.INITIAL_ARRIVALS, len(waiting)))

        spawned = []

        for aircraft in selected:

            self.db.set_status(
                aircraft["callsign"],
                "ARRIVAL"
            )

            spawned.append(aircraft)

        self.initial_arrivals_spawned = True

        self.arrival_timer = 0.0

        self.next_arrival_interval = random.uniform(
            *self.active_wave[2]
        )

        return spawned

    def update(self, dt, can_release_arrival=None, active_aircraft_count=0):

        spawned = []
        self._advance_wave(dt)

        now = time.time()
        for aircraft in self.db.get_due_completions(now):
            self.db.set_status(aircraft["callsign"], "WAITING_ARRIVAL")
            self.db.set_arrival_time(aircraft["callsign"], None)

        if not self.initial_arrivals_spawned:

            if (active_aircraft_count >= self.active_wave_capacity
                    or active_aircraft_count >= self.MAX_ACTIVE_AIRCRAFT):
                return spawned

            spawned.extend(
                self.spawn_initial_arrivals(dt)
            )

            return spawned

        active_arrivals = self.get_active_arrivals()

        if (active_aircraft_count >= self.active_wave_capacity
                or active_aircraft_count >= self.MAX_ACTIVE_AIRCRAFT):

            self.arrival_timer = 0.0

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

        if can_release_arrival is not None and not can_release_arrival(aircraft):
            return spawned

        self.db.set_status(
            aircraft["callsign"],
            "ARRIVAL"
        )

        spawned.append(
            aircraft
        )

        self.arrival_timer = 0.0

        self.next_arrival_interval = random.uniform(
            *self.active_wave[2]
        )

        return spawned

    def mark_landed(self, callsign):

        self.db.mark_landed(
            callsign
        )

    def mark_departing(self, callsign):

        self.db.mark_departing(
            callsign
        )

    def mark_completed(self, callsign):

        self.db.set_status(
            callsign,
            "WAITING_ARRIVAL"
        )

    def mark_arrival_available(self, callsign):

        self.db.set_status(
            callsign,
            "WAITING_ARRIVAL"
        )

    def mark_parked(self, callsign):

        self.db.set_status(
            callsign,
            "AIRPORT"
        )
