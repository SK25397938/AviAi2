from pathlib import Path
import requests
from .config import *

DOWNLOADS = {
    "airports.csv": AIRPORTS_URL,
    "runways.csv": RUNWAYS_URL,
    "navaids.csv": NAVAIDS_URL,
    "airport_frequencies.csv": FREQUENCIES_URL
}

class Downloader:

    def __init__(self):
        self.folder = Path("data/downloads")
        self.folder.mkdir(
            parents=True,
            exist_ok=True
        )

    def download(self):
        for filename, url in DOWNLOADS.items():
            print(f"Downloading {filename}")
            response = requests.get(
                url,
                timeout=60
            )
            response.raise_for_status()
            with open(
                self.folder / filename,
                "wb"
            ) as file:
                file.write(
                    response.content
                )
        print()
        print("Download Complete")