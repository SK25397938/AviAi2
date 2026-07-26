import json
from pathlib import Path


class AirportExporter:

    def __init__(self, airport):

        self.airport = airport

    def save(self, icao):

        Path("data/airports").mkdir(
            exist_ok=True
        )

        path = Path(
            f"data/airports/{icao}.json"
        )

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                self.airport,
                f,
                indent=4
            )

        return path