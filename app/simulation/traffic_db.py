import sqlite3
from pathlib import Path


class TrafficDatabase:

    def __init__(self):

        Path("data/simulation").mkdir(
            parents=True,
            exist_ok=True
        )

        self.conn = sqlite3.connect(
            "data/simulation/traffic.sqlite"
        )

        self.cursor = self.conn.cursor()

        self.create()

    def create(self):

        self.cursor.execute("""
CREATE TABLE IF NOT EXISTS traffic_aircraft(
    callsign TEXT PRIMARY KEY,
    aircraft_type TEXT NOT NULL,
    airline TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    arrival_time REAL,
    departure_time REAL,
    turnaround_until REAL
)
""")

        columns = {
            "arrival_time": "REAL",
            "departure_time": "REAL",
            "turnaround_until": "REAL"
        }

        for column, column_type in columns.items():

            try:

                self.cursor.execute(
                    f"""
ALTER TABLE traffic_aircraft
ADD COLUMN {column} {column_type}
"""
                )

            except sqlite3.OperationalError:

                pass

        self.conn.commit()

    def upsert(
        self,
        callsign,
        aircraft_type,
        airline,
        origin,
        destination,
        side="WEST",
        status="ARRIVAL",
        arrival_time=None,
        departure_time=None,
        turnaround_until=None
    ):

        self.cursor.execute("""
INSERT INTO traffic_aircraft(
    callsign,
    aircraft_type,
    airline,
    origin,
    destination,
    side,
    status,
    arrival_time,
    departure_time,
    turnaround_until
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

ON CONFLICT(callsign)
DO UPDATE SET
    aircraft_type = excluded.aircraft_type,
    airline = excluded.airline,
    origin = excluded.origin,
    destination = excluded.destination,
    side = excluded.side,
    status = excluded.status,
    arrival_time = excluded.arrival_time,
    departure_time = excluded.departure_time,
    turnaround_until = excluded.turnaround_until
""", (
            callsign,
            aircraft_type,
            airline,
            origin,
            destination,
            side,
            status,
            arrival_time,
            departure_time,
            turnaround_until
        ))

        self.conn.commit()

    def _row_to_dict(self, row):

        return {
            "callsign": row[0],
            "aircraft_type": row[1],
            "airline": row[2],
            "origin": row[3],
            "destination": row[4],
            "side": row[5],
            "status": row[6],
            "arrival_time": row[7],
            "departure_time": row[8],
            "turnaround_until": row[9]
        }

    def get(self, callsign):

        self.cursor.execute("""
SELECT
    callsign,
    aircraft_type,
    airline,
    origin,
    destination,
    side,
    status,
    arrival_time,
    departure_time,
    turnaround_until
FROM traffic_aircraft
WHERE callsign = ?
""", (callsign,))

        row = self.cursor.fetchone()

        if row is None:
            return None

        return self._row_to_dict(row)

    def get_by_status(self, status):

        self.cursor.execute("""
SELECT
    callsign,
    aircraft_type,
    airline,
    origin,
    destination,
    side,
    status,
    arrival_time,
    departure_time,
    turnaround_until
FROM traffic_aircraft
WHERE status = ?
""", (status,))

        rows = self.cursor.fetchall()

        return [
            self._row_to_dict(row)
            for row in rows
        ]

    def get_available_arrivals(self, current_time=None):

        if current_time is None:

            return self.get_by_status("ARRIVAL")

        self.cursor.execute("""
SELECT
    callsign,
    aircraft_type,
    airline,
    origin,
    destination,
    side,
    status,
    arrival_time,
    departure_time,
    turnaround_until
FROM traffic_aircraft
WHERE status = 'ARRIVAL'
AND (
    arrival_time IS NULL
    OR arrival_time <= ?
)
""", (current_time,))

        rows = self.cursor.fetchall()

        return [
            self._row_to_dict(row)
            for row in rows
        ]

    def get_airport_aircraft(self):

        return self.get_by_status("AIRPORT")

    def get_available_departures(self, current_time=None):

        if current_time is None:

            return self.get_by_status("DEPARTURE")

        self.cursor.execute("""
SELECT
    callsign,
    aircraft_type,
    airline,
    origin,
    destination,
    side,
    status,
    arrival_time,
    departure_time,
    turnaround_until
FROM traffic_aircraft
WHERE status = 'AIRPORT'
AND (
    departure_time IS NULL
    OR departure_time <= ?
)
""", (current_time,))

        rows = self.cursor.fetchall()

        return [
            self._row_to_dict(row)
            for row in rows
        ]

    def get_departures(self):

        return self.get_by_status("DEPARTURE")

    def get_completed(self):

        return self.get_by_status("COMPLETED")

    def set_status(
        self,
        callsign,
        status
    ):

        self.cursor.execute("""
UPDATE traffic_aircraft
SET status = ?
WHERE callsign = ?
""", (
            status,
            callsign
        ))

        self.conn.commit()

    def set_arrival_time(
        self,
        callsign,
        arrival_time
    ):

        self.cursor.execute("""
UPDATE traffic_aircraft
SET arrival_time = ?
WHERE callsign = ?
""", (
            arrival_time,
            callsign
        ))

        self.conn.commit()

    def set_departure_time(
        self,
        callsign,
        departure_time
    ):

        self.cursor.execute("""
UPDATE traffic_aircraft
SET departure_time = ?
WHERE callsign = ?
""", (
            departure_time,
            callsign
        ))

        self.conn.commit()

    def set_turnaround_until(
        self,
        callsign,
        turnaround_until
    ):

        self.cursor.execute("""
UPDATE traffic_aircraft
SET turnaround_until = ?
WHERE callsign = ?
""", (
            turnaround_until,
            callsign
        ))

        self.conn.commit()

    def mark_landed(
        self,
        callsign,
        turnaround_until=None
    ):

        self.cursor.execute("""
UPDATE traffic_aircraft
SET
    status = 'AIRPORT',
    turnaround_until = ?,
    departure_time = ?
WHERE callsign = ?
""", (
            turnaround_until,
            turnaround_until,
            callsign
        ))

        self.conn.commit()

    def mark_departing(
        self,
        callsign
    ):

        self.set_status(
            callsign,
            "DEPARTURE"
        )

    def mark_completed(
        self,
        callsign
    ):

        self.set_status(
            callsign,
            "COMPLETED"
        )

    def return_to_arrival(
        self,
        callsign,
        arrival_time=None
    ):

        self.cursor.execute("""
UPDATE traffic_aircraft
SET
    status = 'ARRIVAL',
    arrival_time = ?,
    departure_time = NULL,
    turnaround_until = NULL
WHERE callsign = ?
""", (
            arrival_time,
            callsign
        ))

        self.conn.commit()

    def close(self):

        self.conn.close()