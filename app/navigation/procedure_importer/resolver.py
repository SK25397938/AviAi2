import sqlite3


class WaypointResolver:

    def __init__(self):

        self.db = sqlite3.connect(
            "data/navdb/navdb.sqlite"
        )

        self.cursor = self.db.cursor()

    def airport(self, icao):

        return self.cursor.execute(
            """
            SELECT *

            FROM airports

            WHERE icao=?
            """,
            (icao.upper(),)
        ).fetchone()

    def runway(self, airport, runway):

        return self.cursor.execute(
            """
            SELECT *

            FROM runways

            WHERE airport=?

            AND runway=?
            """,
            (
                airport.upper(),
                runway.upper()
            )
        ).fetchone()

    def vor(self, ident):

        return self.cursor.execute(
            """
            SELECT *

            FROM vors

            WHERE ident=?
            """,
            (ident.upper(),)
        ).fetchone()

    def ndb(self, ident):

        return self.cursor.execute(
            """
            SELECT *

            FROM ndbs

            WHERE ident=?
            """,
            (ident.upper(),)
        ).fetchone()

    def close(self):

        self.db.close()