from dataclasses import dataclass


@dataclass
class AircraftPerformance:
    category: str
    approach_speed: int
    max_bank: float
    descent_rate: int


AIRCRAFT = {
    "A320": AircraftPerformance(
        category="C",
        approach_speed=145,
        max_bank=25,
        descent_rate=700
    ),

    "B738": AircraftPerformance(
        category="C",
        approach_speed=145,
        max_bank=25,
        descent_rate=700
    ),

    "A359": AircraftPerformance(
        category="E",
        approach_speed=155,
        max_bank=20,
        descent_rate=800
    ),

    "B77W": AircraftPerformance(
        category="E",
        approach_speed=155,
        max_bank=20,
        descent_rate=800
    ),

    "A388": AircraftPerformance(
        category="F",
        approach_speed=165,
        max_bank=18,
        descent_rate=900
    )
}