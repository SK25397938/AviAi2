class DescentManager:

    def target_altitude(
        self,
        distance_nm
    ):

        if distance_nm >= 25:
            return 18000

        if distance_nm >= 20:
            return 15000

        if distance_nm >= 16:
            return 12000

        if distance_nm >= 12:
            return 9000

        if distance_nm >= 10:
            return 7000

        if distance_nm >= 8:
            return 6000

        if distance_nm >= 6:
            return 5000

        if distance_nm >= 4:
            return 3500

        if distance_nm >= 2:
            return 2000

        return 50