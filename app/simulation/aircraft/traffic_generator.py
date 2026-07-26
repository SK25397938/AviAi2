from typing import Set
from app.simulation.aircraft.models import SimulatedAircraft
from app.simulation.flights.models import Flight
from app.simulation.flights.schedules import FlightSchedule
from app.simulation.world import simulation_world
from app.simulation.clock import simulation_clock


class TrafficGenerator:
    def __init__(self, airport_code: str):
        self.airport_code = airport_code.upper()
        self.schedule = FlightSchedule(self.airport_code)
        self.flights = []
        self.spawned_flights: Set[str] = set()

    def load_schedule(self):
        self.flights = self.schedule.load()

    def create_arrival(
        self,
        flight: Flight,
        latitude: float,
        longitude: float,
        altitude_ft: float,
        heading_deg: float
    ):
        aircraft = SimulatedAircraft(
            id=flight.callsign,
            callsign=flight.callsign,
            flight_number=flight.flight_number,
            airline=flight.airline_name,
            aircraft_type=flight.aircraft_type,
            wake_category=flight.wake_category,
            origin=flight.origin,
            destination=flight.destination,
            latitude=latitude,
            longitude=longitude,
            altitude_ft=altitude_ft,
            speed_kts=250,
            heading_deg=heading_deg,
            vertical_speed_fpm=-1000,
            phase="ARRIVAL",
            status="ACTIVE",
            controller="APPROACH",
            assigned_runway=flight.assigned_runway,
            assigned_gate=flight.assigned_gate
        )

        simulation_world.add_aircraft(aircraft)
        self.spawned_flights.add(flight.flight_number)

        return aircraft

    def create_departure(
        self,
        flight: Flight,
        latitude: float,
        longitude: float
    ):
        aircraft = SimulatedAircraft(
            id=flight.callsign,
            callsign=flight.callsign,
            flight_number=flight.flight_number,
            airline=flight.airline_name,
            aircraft_type=flight.aircraft_type,
            wake_category=flight.wake_category,
            origin=flight.origin,
            destination=flight.destination,
            latitude=latitude,
            longitude=longitude,
            altitude_ft=0,
            speed_kts=0,
            heading_deg=0,
            vertical_speed_fpm=0,
            phase="PARKED",
            status="ACTIVE",
            controller="GROUND",
            assigned_runway=flight.assigned_runway,
            assigned_gate=flight.assigned_gate
        )

        simulation_world.add_aircraft(aircraft)
        self.spawned_flights.add(flight.flight_number)

        return aircraft

    def get_pending_flights(self):
        current_time = simulation_clock.get_time()

        return [
            flight
            for flight in self.flights
            if flight.scheduled_time <= current_time
            and flight.flight_number not in self.spawned_flights
        ]

    def mark_spawned(self, flight_number: str):
        self.spawned_flights.add(flight_number)

    def reset(self):
        self.spawned_flights.clear()


traffic_generator = TrafficGenerator("VABB")