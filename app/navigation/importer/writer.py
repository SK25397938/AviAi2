import json
from pathlib import Path


class NavigationWriter:

    def __init__(self, airport):

        self.base = Path(
            f"data/navigation/{airport}"
        )

    def save(self, filename, data):

        with open(
            self.base / filename,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=4
            )