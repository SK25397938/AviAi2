import json
from datetime import datetime
from pathlib import Path
from typing import List

from app.simulation.flights.models import Flight


ROOT_DIR = Path(__file__).resolve().parents[3]
FLIGHT_DATA_DIR = ROOT_DIR / "data" / "simulation" / "flights"


class FlightSchedule:
    def __init__(self, airport_code: str):
        self.airport_code = airport_code.upper()
        self.flights: List[Flight] = []

    def load(self):
        file_path = FLIGHT_DATA_DIR / f"{self.airport_code}.json"

        if not file_path.exists():
            raise FileNotFoundError(
                f"Flight schedule not found for {self.airport_code}"
            )

        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)

        self.flights = [
            Flight(
                flight_number=item["flight_number"],
                callsign=item["callsign"],
                airline_code=item["airline_code"],
                airline_name=item["airline_name"],
                aircraft_type=item["aircraft_type"],
                wake_category=item["wake_category"],
                origin=item["origin"],
                destination=item["destination"],
                scheduled_time=datetime.fromisoformat(
                    item["scheduled_time"]
                ),
                operation_type=item["operation_type"],
                terminal=item.get("terminal"),
                assigned_gate=item.get("assigned_gate"),
                assigned_runway=item.get("assigned_runway")
            )
            for item in data
        ]

        return self.flights

    def get_all(self):
        return self.flights

    def get_arrivals(self):
        return [
            flight
            for flight in self.flights
            if flight.operation_type == "ARRIVAL"
        ]

    def get_departures(self):
        return [
            flight
            for flight in self.flights
            if flight.operation_type == "DEPARTURE"
        ]

    def get_flights_between(self, start: datetime, end: datetime):
        return [
            flight
            for flight in self.flights
            if start <= flight.scheduled_time <= end
        ]