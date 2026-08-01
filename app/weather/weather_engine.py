import re
import requests
from app.weather.models import WeatherData

class WeatherEngine:

    def __init__(self, icao: str):
        self.icao = icao.upper()

    def fetch(self):
        url = f"https://tgftp.nws.noaa.gov/data/observations/metar/stations/{self.icao}.TXT"
        r = requests.get(url, timeout=10)
        lines = r.text.strip().splitlines()
        observation_time = lines[0]
        metar = lines[1]
        tokens = metar.split()
        wind = next(x for x in tokens if x.endswith("KT"))
        m = re.match(r"(\d{3})(\d{2})(G(\d{2}))?KT", wind)
        wind_direction = int(m.group(1))
        wind_speed = int(m.group(2))
        wind_gust = int(m.group(4)) if m.group(4) else None
        visibility = 9999
        for token in tokens:
            if token.isdigit():
                visibility = int(token)
                break
        temperature = 0
        dewpoint = 0
        for token in tokens:
            if "/" in token and (token[0].isdigit() or token.startswith("M")):
                t, d = token.split("/")
                temperature = -int(t[1:]) if t.startswith("M") else int(t)
                dewpoint = -int(d[1:]) if d.startswith("M") else int(d)
                break
        qnh = int(next(x for x in tokens if x.startswith("Q")).replace("Q", ""))
        clouds = [
            x for x in tokens
            if x.startswith(("FEW", "SCT", "BKN", "OVC"))
        ]
        condition = "VMC"
        if "FG" in tokens:
            condition = "FOG"
        elif "BR" in tokens:
            condition = "MIST"
        elif "TS" in metar:
            condition = "THUNDERSTORM"
        elif "RA" in metar:
            condition = "RAIN"
        ceiling = 99999
        for cloud in clouds:
            if cloud.startswith(("BKN", "OVC")):
                h = re.findall(r"\d{3}", cloud)
                if h:
                    ceiling = min(ceiling, int(h[0]) * 100)
        if visibility >= 8000 and ceiling > 3000:
            flight_category = "VFR"
        elif visibility >= 5000 and ceiling >= 1000:
            flight_category = "MVFR"
        elif visibility >= 1600 and ceiling >= 500:
            flight_category = "IFR"
        else:
            flight_category = "LIFR"
        return WeatherData(
            icao=self.icao,
            observation_time=observation_time,
            raw_metar=metar,
            wind_direction=wind_direction,
            wind_speed=wind_speed,
            wind_gust=wind_gust,
            visibility=visibility,
            clouds=clouds,
            temperature=temperature,
            dewpoint=dewpoint,
            qnh=qnh,
            condition=condition,
            flight_category=flight_category
        )