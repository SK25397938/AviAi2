import json
from pathlib import Path
from math import radians
from math import sin
from math import cos
from math import sqrt
from math import atan2

from app.intelligence.instruction_manager import instruction_manager


class ApproachAI:

    CAPTURE_DISTANCE_KM = 0.35

    def __init__(
        self,
        runway
    ):

        self.runway = runway

        path = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "semantics"
            / "vabb_approach.json"
        )

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            self.approach_data = json.load(f)

        self.approach_points = (
            self.approach_data["approach"]
        )

        self.speed_profiles = (
            self.approach_data["speed_profiles"]
        )

    def update(
        self,
        aircraft
    ):

        if aircraft.phase != "FINAL":

            return

        aircraft.state = "APPROACH"

        if not hasattr(
            aircraft,
            "approach_index"
        ):

            aircraft.approach_index = (
                self._closest_waypoint_index(
                    aircraft.lat,
                    aircraft.lon
                )
            )

            aircraft.approach_instruction_index = None

        if aircraft.approach_index >= len(
            self.approach_points
        ):

            return

        point = self.approach_points[
            aircraft.approach_index
        ]

        aircraft.approach_phase = point["id"]

        heading = self._bearing(
            aircraft.lat,
            aircraft.lon,
            point["latitude"],
            point["longitude"]
        )

        aircraft.assign_heading(
            heading
        )

        aircraft.assign_altitude(
            point["altitude_ft"]
        )

        speed = self._get_speed(
            aircraft,
            point["id"]
        )

        if speed is not None:

            aircraft.assign_speed(
                speed
            )

        distance = self._distance(
            aircraft.lat,
            aircraft.lon,
            point["latitude"],
            point["longitude"]
        )

        aircraft.current_distance = distance

        if (
            aircraft.approach_instruction_index
            != aircraft.approach_index
        ):

            instruction_manager.issue(
                aircraft,
                "Approach",
                f"Proceed to {point['id']}, "
                f"maintain {point['altitude_ft']} feet, "
                f"{speed} knots."
            )

            aircraft.approach_instruction_index = (
                aircraft.approach_index
            )

        if distance <= self.CAPTURE_DISTANCE_KM:

            aircraft.approach_index += 1

            aircraft.approach_instruction_index = None

            if aircraft.approach_index >= len(
                self.approach_points
            ):

                aircraft.state = "APPROACH_COMPLETE"
                aircraft.phase = "APPROACH_COMPLETE"
                aircraft.approach_complete = True

    def _closest_waypoint_index(
        self,
        latitude,
        longitude
    ):

        closest_index = 0
        closest_distance = float("inf")

        for index, point in enumerate(
            self.approach_points
        ):

            distance = self._distance(
                latitude,
                longitude,
                point["latitude"],
                point["longitude"]
            )

            if distance < closest_distance:

                closest_distance = distance
                closest_index = index

        return closest_index

    def _bearing(
        self,
        lat1,
        lon1,
        lat2,
        lon2
    ):

        lat1 = radians(lat1)
        lat2 = radians(lat2)

        dlon = radians(
            lon2 - lon1
        )

        y = (
            sin(dlon)
            * cos(lat2)
        )

        x = (
            cos(lat1)
            * sin(lat2)
            -
            sin(lat1)
            * cos(lat2)
            * cos(dlon)
        )

        return (
            atan2(y, x)
            * 180
            / 3.141592653589793
            + 360
        ) % 360

    def _get_speed(
        self,
        aircraft,
        point_id
    ):

        aircraft_type = aircraft.aircraft_type

        profile = self.speed_profiles.get(
            aircraft_type
        )

        if profile is None:

            aliases = {
                "B77W": "B777-300ER",
                "B77L": "B777-200LR",
                "B772": "B777-200",
                "B77F": "B777F",
                "B748": "B747-8",
                "B748F": "B747-8F",
                "A388": "A388",
                "A359": "A359",
                "A35K": "A350-1000",
                "A320": "A320",
                "A321": "A321",
                "B788": "B787-8",
                "B789": "B787-9",
                "B78X": "B787-10"
            }

            mapped_type = aliases.get(
                aircraft_type
            )

            if mapped_type is not None:

                profile = self.speed_profiles.get(
                    mapped_type
                )

        if profile is None:

            profile = self.speed_profiles.get(
                "A320"
            )

        if profile is None:

            return None

        return profile.get(
            point_id
        )

    def _distance(
        self,
        lat1,
        lon1,
        lat2,
        lon2
    ):

        earth_radius_km = 6371.0

        dlat = radians(
            lat2 - lat1
        )

        dlon = radians(
            lon2 - lon1
        )

        a = (
            sin(dlat / 2) ** 2
            +
            cos(radians(lat1))
            * cos(radians(lat2))
            * sin(dlon / 2) ** 2
        )

        c = 2 * atan2(
            sqrt(a),
            sqrt(1 - a)
        )

        return earth_radius_km * c