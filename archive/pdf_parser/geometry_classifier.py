from math import sqrt, atan2, degrees
from app.parser.geometry import length, angle

class GeometryClassifier:

    def __init__(self, vectors):
        self.vectors = vectors

    def classify(self):

        runway_candidates = []
        taxiway_candidates = []
        small_geometry = []

        for obj in self.vectors:

            if obj["type"] != "line":
                continue

            line = obj.copy()

            line["length"] = length(line)
            line["angle"] = angle(line)

            if line["length"] > 120:

                runway_candidates.append(line)

            elif line["length"] > 20:

                taxiway_candidates.append(line)

            else:

                small_geometry.append(line)

        return {
            "runways": runway_candidates,
            "taxiways": taxiway_candidates,
            "small": small_geometry
        }