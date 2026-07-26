import json
import time
from collections import deque
from app.core.config import redis_client

MAX_POINTS = 6
WINDOW_SECONDS = 40


def update_trajectory(icao: str, altitude: float):
    if altitude is None:
        return None

    key = f"trajectory:{icao}"
    now = time.time()

    raw = redis_client.get(key)

    if raw:
        history = deque(json.loads(raw), maxlen=MAX_POINTS)
    else:
        history = deque(maxlen=MAX_POINTS)

    history.append((now, altitude))

    redis_client.setex(
        key,
        WINDOW_SECONDS,
        json.dumps(list(history))
    )

    return list(history)


def analyze_vertical_trend(history):
    if not history or len(history) < 3:
        return "UNKNOWN"

    try:
        # New format: (timestamp, altitude)
        t_start, alt_start = history[0]
        t_end, alt_end = history[-1]

        dt = t_end - t_start
        if dt <= 0:
            return "UNKNOWN"

        rate = (alt_end - alt_start) / dt

    except Exception:
        # Old format fallback: altitude-only
        alt_start = history[0]
        alt_end = history[-1]
        rate = alt_end - alt_start

    if rate > 2.0:
        return "RAPID_CLIMB"
    elif rate > 0.5:
        return "ASCENDING"
    elif rate < -2.0:
        return "RAPID_DESCENT"
    elif rate < -0.5:
        return "DESCENDING"
    else:
        return "LEVEL"
