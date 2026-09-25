import sqlite3
from pathlib import Path


class AircraftDatabase:

    def __init__(self):

        Path("data/simulation").mkdir(
            parents=True,
            exist_ok=True
        )

        self.conn = sqlite3.connect(
            "data/simulation/aircraft.sqlite"
        )

        self.cursor = self.conn.cursor()

        self.create()

    def create(self):

        self.cursor.execute("""
CREATE TABLE IF NOT EXISTS aircraft_operations(
    callsign TEXT PRIMARY KEY,
    aircraft_type TEXT NOT NULL,
    runway_exit TEXT,
    taxiway TEXT,
    parking_stand TEXT
)
""")

        self.conn.commit()

    def upsert(
        self,
        callsign,
        aircraft_type,
        runway_exit=None,
        taxiway=None,
        parking_stand=None
    ):

        self.cursor.execute("""
INSERT INTO aircraft_operations (
    callsign,
    aircraft_type,
    runway_exit,
    taxiway,
    parking_stand
)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(callsign)
DO UPDATE SET
    aircraft_type = excluded.aircraft_type,
    runway_exit = COALESCE(
        excluded.runway_exit,
        aircraft_operations.runway_exit
    ),
    taxiway = COALESCE(
        excluded.taxiway,
        aircraft_operations.taxiway
    ),
    parking_stand = COALESCE(
        excluded.parking_stand,
        aircraft_operations.parking_stand
    )
""", (
            callsign,
            aircraft_type,
            runway_exit,
            taxiway,
            parking_stand
        ))

        self.conn.commit()

    def get(
        self,
        callsign
    ):

        self.cursor.execute("""
SELECT
    callsign,
    aircraft_type,
    runway_exit,
    taxiway,
    parking_stand
FROM aircraft_operations
WHERE callsign = ?
""", (callsign,))

        row = self.cursor.fetchone()

        if row is None:
            return None

        return {
            "callsign": row[0],
            "aircraft_type": row[1],
            "runway_exit": row[2],
            "taxiway": row[3],
            "parking_stand": row[4]
        }

    def close(self):

        self.conn.close()