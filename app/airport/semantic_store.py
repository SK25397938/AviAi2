import json
from pathlib import Path


class SemanticStore:

    def __init__(self, icao):

        self.path = Path(f"data/semantics/{icao.upper()}.json")

    def load(self):

        if not self.path.exists():

            return {
                "taxiways": [],
                "aprons": [],
                "runway_entries": [],
                "holding_points": [],
                "aliases": {}
            }

        with open(self.path, encoding="utf-8") as f:

            return json.load(f)

    def save(self, data):

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with open(
            self.path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=4
            )