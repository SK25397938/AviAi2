from dataclasses import dataclass
from math import cos
from math import radians
from math import sin
from math import sqrt

from .geometry import destination_point


@dataclass
class Runway:

    ident: str

    threshold_lat: float
    threshold_lon: float

    heading: float

    length_nm: float

    width_m: float = 45

    glide_angle: float = 3.0

    touchdown_distance_nm: float = 0.25

    def threshold(self):

        return (
            self.threshold_lat,
            self.threshold_lon
        )

    def centerline_heading(self):

        return self.heading

    def opposite_heading(self):

        return (
            self.heading + 180
        ) % 360

    def touchdown_point(self):

        return destination_point(
            self.threshold_lat,
            self.threshold_lon,
            self.heading,
            self.touchdown_distance_nm
        )

    def localizer_point(
        self,
        distance_nm=10
    ):

        return destination_point(
            self.threshold_lat,
            self.threshold_lon,
            self.opposite_heading(),
            distance_nm
        )

    def distance_to_threshold(
        self,
        lat,
        lon
    ):

        dx = (
            lon - self.threshold_lon
        ) * cos(
            radians(self.threshold_lat)
        ) * 60

        dy = (
            lat - self.threshold_lat
        ) * 60

        return sqrt(
            dx * dx +
            dy * dy
        )

    def distance_to_touchdown(
        self,
        lat,
        lon
    ):

        td_lat, td_lon = self.touchdown_point()

        dx = (
            lon - td_lon
        ) * cos(
            radians(td_lat)
        ) * 60

        dy = (
            lat - td_lat
        ) * 60

        return sqrt(
            dx * dx +
            dy * dy
        )

    def along_track_distance(
        self,
        lat,
        lon
    ):

        dx = (
            lon - self.threshold_lon
        ) * cos(
            radians(self.threshold_lat)
        ) * 60

        dy = (
            lat - self.threshold_lat
        ) * 60

        theta = radians(
            self.opposite_heading()
        )

        return (
            dx * sin(theta) +
            dy * cos(theta)
        )

    def cross_track_error(
        self,
        lat,
        lon
    ):

        dx = (
            lon - self.threshold_lon
        ) * cos(
            radians(self.threshold_lat)
        ) * 60

        dy = (
            lat - self.threshold_lat
        ) * 60

        theta = radians(
            self.opposite_heading()
        )

        return abs(

            dx * cos(theta)

            -

            dy * sin(theta)

        )

    def glidepath_altitude(
        self,
        distance_nm
    ):

        altitude = round(
            max(distance_nm, 0) * 318
        )

        return max(
            altitude,
            50
        )