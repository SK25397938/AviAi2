import threading
import time

from .weather_engine import WeatherEngine


def loop():

    while True:

        try:

            WeatherEngine("VABB").fetch()

        except Exception as e:

            print("[WEATHER]", e)

        time.sleep(60)


def start():

    threading.Thread(

        target=loop,

        daemon=True

    ).start()