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
    EARTH_RADIUS_KM = 6371.0

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

        # The arrival route JSON contains the ordered final approach fixes.
        # Guidance has already flown them when it hands off at RWY27.
        if (aircraft.route and aircraft.route_index >= len(aircraft.route) - 1
                and str(getattr(aircraft, "assigned_node", "")).upper() == "RWY27"):
            aircraft.state = "APPROACH_COMPLETE"
            aircraft.phase = "APPROACH_COMPLETE"
            aircraft.approach_complete = True
            aircraft.target_node = None
            aircraft.clearance.direct_node = None
            return

        aircraft.state = "APPROACH"

        if not hasattr(
            aircraft,
            "approach_index"
        ):
            selected_entry = getattr(aircraft, "_approach_entry_index", None)
            aircraft.approach_index = (
                selected_entry if isinstance(selected_entry, int)
                and 0 <= selected_entry < len(self.approach_points)
                else self._closest_waypoint_index(aircraft.lat, aircraft.lon)
            )

            aircraft.approach_instruction_index = None
            transition = self.approach_points[aircraft.approach_index]["id"]
            print(f"APPROACH INTERCEPT | callsign={aircraft.callsign} target={transition}")

        if aircraft.approach_index >= len(
            self.approach_points
        ):

            return

        point = self.approach_points[
            aircraft.approach_index
        ]

        aircraft.target_node = point["id"]
        aircraft.clearance.direct_node = point["id"]
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

        distance_nm = self._distance(
            aircraft.lat, aircraft.lon, point["latitude"], point["longitude"]
        ) / 1.852
        current_speed = max(float(getattr(aircraft, "speed_kts", 1) or 1), 1.0)
        minutes_to_fix = max(distance_nm * 60.0 / current_speed, 0.1)
        required_vertical_speed = (
            (float(point["altitude_ft"]) - float(aircraft.altitude_ft)) / minutes_to_fix
        )
        aircraft.target_vertical_speed_fpm = max(-1500.0, min(1500.0, required_vertical_speed))

        speed = self._get_speed(
            aircraft,
            point["id"]
        )

        if speed is not None:
            sequencing_ceiling = getattr(aircraft, "_sequence_speed_ceiling", None)
            if isinstance(sequencing_ceiling, (int, float)):
                speed = min(float(speed), float(sequencing_ceiling))
            aircraft.assign_speed(speed)

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

            if point["id"] == "FAF":
                print(f"FINAL APPROACH | callsign={aircraft.callsign}")

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

        previous_position = getattr(aircraft, "_approach_previous_position", None)
        current_position = (float(aircraft.lat), float(aircraft.lon))
        aircraft._approach_previous_position = current_position
        passed_within_capture = (
            previous_position is not None
            and self._distance_to_segment_km(
                point["latitude"], point["longitude"], previous_position, current_position
            ) <= self.CAPTURE_DISTANCE_KM
        )

        if distance <= self.CAPTURE_DISTANCE_KM or passed_within_capture:

            # ApproachAI owns this route cursor once cleared. Keep the last
            # captured fix and the active guidance target in sync.
            aircraft.assigned_node = point["id"]

            aircraft.approach_index += 1

            aircraft.approach_instruction_index = None

            if aircraft.approach_index >= len(
                self.approach_points
            ):

                aircraft.state = "APPROACH_COMPLETE"
                aircraft.phase = "APPROACH_COMPLETE"
                aircraft.approach_complete = True
                aircraft.target_node = None
                aircraft.clearance.direct_node = None
            else:
                next_point = self.approach_points[aircraft.approach_index]
                aircraft.target_node = next_point["id"]
                aircraft.clearance.direct_node = next_point["id"]

    @classmethod
    def _distance_to_segment_km(cls, latitude, longitude, start, end):
        """Return the local-plane distance from a fix to a sampled flight segment."""
        reference_latitude = radians(latitude)
        scale_x = cls.EARTH_RADIUS_KM * cos(reference_latitude) * radians(1.0)
        scale_y = cls.EARTH_RADIUS_KM * radians(1.0)

        def project(position):
            return (
                (float(position[1]) - longitude) * scale_x,
                (float(position[0]) - latitude) * scale_y,
            )

        start_x, start_y = project(start)
        end_x, end_y = project(end)
        segment_x, segment_y = end_x - start_x, end_y - start_y
        length_squared = segment_x * segment_x + segment_y * segment_y
        if length_squared == 0:
            return sqrt(start_x * start_x + start_y * start_y)
        fraction = max(0.0, min(1.0, -(start_x * segment_x + start_y * segment_y) / length_squared))
        closest_x = start_x + fraction * segment_x
        closest_y = start_y + fraction * segment_y
        return sqrt(closest_x * closest_x + closest_y * closest_y)

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
                "A350-900": "A359",
                "A350-1000": "A350-1000",
                "A380": "A388",
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
