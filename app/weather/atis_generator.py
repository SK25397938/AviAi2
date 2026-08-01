from .models import AtisData


class AtisGenerator:

    def generate(self, weather, runway):

        wind = f"{weather.wind_direction:03d}/{weather.wind_speed}KT"

        message = (
            f"Information Alpha. "
            f"Wind {wind}. "
            f"Visibility {weather.visibility}. "
            f"{' '.join(weather.clouds)}. "
            f"Temperature {weather.temperature}. "
            f"Dewpoint {weather.dewpoint}. "
            f"QNH {weather.qnh}. "
            f"Runway {runway.active_runway} in use."
        )

        return AtisData(
            information="A",
            airport=weather.icao,
            observation_time=weather.observation_time,
            wind=wind,
            visibility=str(weather.visibility),
            weather=weather.condition,
            clouds=weather.clouds,
            temperature=weather.temperature,
            dewpoint=weather.dewpoint,
            qnh=weather.qnh,
            arrival_runway=runway.arrival_runway,
            departure_runway=runway.departure_runway,
            remarks=[],
            message=message
        )