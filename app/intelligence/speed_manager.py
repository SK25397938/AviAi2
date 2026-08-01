class SpeedManager:

    def target_speed(
        self,
        distance_nm
    ):

        if distance_nm > 50:
            return 300

        if distance_nm > 40:
            return 280

        if distance_nm > 30:
            return 250

        if distance_nm > 20:
            return 220

        if distance_nm > 10:
            return 180

        return 160