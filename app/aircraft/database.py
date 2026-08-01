import json
from pathlib import Path


PROFILE_FILE = Path(__file__).parent / "profiles.json"


with open(PROFILE_FILE, "r") as f:
    AIRCRAFT = json.load(f)


def get_aircraft(aircraft_type):

    if aircraft_type not in AIRCRAFT:
        raise ValueError(f"Unknown aircraft type: {aircraft_type}")

    return AIRCRAFT[aircraft_type]