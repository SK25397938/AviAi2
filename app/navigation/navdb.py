import sqlite3
from pathlib import Path


class NavDatabase:

    def __init__(self):

        Path("data/navdb").mkdir(
            parents=True,
            exist_ok=True
        )

        self.conn = sqlite3.connect(
            "data/navdb/navdb.sqlite"
        )

        self.cursor = self.conn.cursor()

    def create(self):

        self.cursor.execute("""
CREATE TABLE IF NOT EXISTS waypoints(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ident TEXT UNIQUE,
    name TEXT,
    type TEXT,
    region TEXT,
    airport TEXT,
    latitude REAL,
    longitude REAL
)
""")

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS vors(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            ident TEXT UNIQUE,

            frequency REAL,

            latitude REAL,

            longitude REAL
        )
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS ndbs(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            ident TEXT UNIQUE,

            frequency REAL,

            latitude REAL,

            longitude REAL
        )
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS airports(

            icao TEXT PRIMARY KEY,

            name TEXT,

            latitude REAL,

            longitude REAL,

            elevation INTEGER
        )
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS runways(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            airport TEXT,

            runway TEXT,

            heading REAL,

            length INTEGER,

            latitude REAL,

            longitude REAL
        )
        """)

        self.conn.commit()

    def close(self):

        self.conn.close()