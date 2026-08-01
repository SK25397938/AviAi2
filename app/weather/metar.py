import requests

def fetch_metar(icao):

    url = f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json"

    r = requests.get(url, timeout=10)

    r.raise_for_status()

    data = r.json()

    if not data:
        return None

    return data[0]