import csv

from app.navigation.navdb import NavDatabase


class DatabaseBuilder:

    def __init__(self):

        self.db = NavDatabase()

        self.db.create()

    def build_airports(self):

        print("Importing Airports...")

        with open(
            "data/downloads/airports.csv",
            encoding="utf-8"
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:

                try:

                    self.db.cursor.execute(
                        """
                        INSERT OR REPLACE INTO airports
                        VALUES(?,?,?,?,?)
                        """,
                        (
                            row["ident"],
                            row["name"],
                            float(row["latitude_deg"]),
                            float(row["longitude_deg"]),
                            int(float(row["elevation_ft"] or 0))
                        )
                    )

                except:

                    continue

        self.db.conn.commit()

        print("✓ Airports Imported")

    def build_navaids(self):

        print("Importing Navaids...")

        with open(
            "data/downloads/navaids.csv",
            encoding="utf-8"
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:

                try:

                    nav_type = row["type"]

                    if nav_type == "NDB":

                        self.db.cursor.execute(
                            """
                            INSERT OR REPLACE INTO ndbs
                            (ident,frequency,latitude,longitude)
                            VALUES(?,?,?,?)
                            """,
                            (
                                row["ident"],
                                float(row["frequency_khz"]),
                                float(row["latitude_deg"]),
                                float(row["longitude_deg"])
                            )
                        )

                    else:

                        self.db.cursor.execute(
                            """
                            INSERT OR REPLACE INTO vors
                            (ident,frequency,latitude,longitude)
                            VALUES(?,?,?,?)
                            """,
                            (
                                row["ident"],
                                float(row["frequency_khz"]),
                                float(row["latitude_deg"]),
                                float(row["longitude_deg"])
                            )
                        )

                except:

                    continue

        self.db.conn.commit()

        print("✓ Navaids Imported")

    def build_runways(self):

        print("Importing Runways...")

        with open(
            "data/downloads/runways.csv",
            encoding="utf-8"
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:

                try:

                    self.db.cursor.execute(
                        """
                        INSERT INTO runways
                        (
                            airport,
                            runway,
                            heading,
                            length,
                            latitude,
                            longitude
                        )
                        VALUES(?,?,?,?,?,?)
                        """,
                        (
                            row["airport_ident"],
                            row["le_ident"],
                            float(row["le_heading_degT"] or 0),
                            int(float(row["length_ft"] or 0)),
                            float(row["le_latitude_deg"] or 0),
                            float(row["le_longitude_deg"] or 0)
                        )
                    )

                except:

                    continue

        self.db.conn.commit()

        print("✓ Runways Imported")

    def close(self):

        self.db.close()