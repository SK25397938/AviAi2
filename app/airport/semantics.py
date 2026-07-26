import json
from pathlib import Path


class AirportSemantics:

    def __init__(self, icao):

        self.icao = icao.upper()

        path = Path(f"data/semantics/{self.icao}.json")

        if path.exists():

            with open(path, encoding="utf-8") as f:

                self.data = json.load(f)

        else:

            self.data = {

                "taxiways": [],

                "aprons": [],

                "runway_entries": [],

                "holding_points": [],

                "aliases": {}

            }

    def taxiways(self):

        return self.data["taxiways"]

    def aprons(self):

        return self.data["aprons"]

    def holding_points(self):

        return self.data["holding_points"]

    def runway_entries(self):

        return self.data["runway_entries"]

    def aliases(self):

        return self.data["aliases"]