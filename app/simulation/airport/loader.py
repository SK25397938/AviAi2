import json
from pathlib import Path

from app.simulation.airport.models import (
    Airport,
    Runway,
    Taxiway,
    Stand,
    HoldingPoint
)


ROOT_DIR = Path(__file__).resolve().parents[3]
AIRPORT_DATA_DIR = ROOT_DIR / "data" / "simulation" / "airports"


class AirportLoader:
    def __init__(self, airport_code: str):
        self.airport_code = airport_code.upper()
        self.airport_dir = AIRPORT_DATA_DIR / self.airport_code

    def load_json(self, filename: str):
        file_path = self.airport_dir / filename

        if not file_path.exists():
            raise FileNotFoundError(
                f"Airport data file not found: {file_path}"
            )

        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def load_runways(self):
        data = self.load_json("runways.geojson")

        return [
            Runway(
                id=feature["properties"]["id"],
                designator=feature["properties"]["designator"],
                opposite_designator=feature["properties"]["opposite_designator"],
                length_m=feature["properties"]["length_m"],
                width_m=feature["properties"]["width_m"],
                surface=feature["properties"]["surface"],
                coordinates=feature["geometry"]["coordinates"],
                active=feature["properties"].get("active", False)
            )
            for feature in data["features"]
        ]

    def load_taxiways(self):
        data = self.load_json("taxiways.geojson")

        return [
            Taxiway(
                id=feature["properties"]["id"],
                name=feature["properties"]["name"],
                coordinates=feature["geometry"]["coordinates"],
                direction=feature["properties"].get(
                    "direction",
                    "BIDIRECTIONAL"
                ),
                status=feature["properties"].get("status", "OPEN")
            )
            for feature in data["features"]
        ]

    def load_stands(self):
        data = self.load_json("stands.geojson")

        stands = []

        for feature in data["features"]:
            longitude, latitude = feature["geometry"]["coordinates"]

            stands.append(
                Stand(
                    id=feature["properties"]["id"],
                    name=feature["properties"]["name"],
                    latitude=latitude,
                    longitude=longitude,
                    terminal=feature["properties"].get("terminal"),
                    status=feature["properties"].get(
                        "status",
                        "AVAILABLE"
                    )
                )
            )

        return stands

    def load_holding_points(self):
        data = self.load_json("holding_points.geojson")

        holding_points = []

        for feature in data["features"]:
            longitude, latitude = feature["geometry"]["coordinates"]

            holding_points.append(
                HoldingPoint(
                    id=feature["properties"]["id"],
                    name=feature["properties"]["name"],
                    latitude=latitude,
                    longitude=longitude,
                    runway=feature["properties"].get("runway"),
                    taxiway=feature["properties"].get("taxiway")
                )
            )

        return holding_points

    def load(self):
        data = self.load_json("airport.json")

        return Airport(
            icao=data["icao"],
            iata=data["iata"],
            name=data["name"],
            latitude=data["latitude"],
            longitude=data["longitude"],
            elevation_ft=data["elevation_ft"],
            runways=self.load_runways(),
            taxiways=self.load_taxiways(),
            stands=self.load_stands(),
            holding_points=self.load_holding_points()
        )