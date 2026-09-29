import json
import math
import time
from collections import deque
from app.core.config import redis_client

MAX_POINTS = 6
WINDOW_SECONDS = 40
MIN_ARRIVAL_SEPARATION_NM = 4.5
MIN_ARRIVAL_SPEED_KTS = 140.0
SEPARATION_SPEED_GAIN = 12.0
MIN_SHARED_WAYPOINT_TIME_GAP = 60.0


def _distance_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3440.065 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def arrival_path_progress(aircraft, graph):
    route = getattr(aircraft, "route", None) or []

    if len(route) < 2:
        return None

    points = []

    for node_id in route:
        try:
            node = graph.get_node(node_id)
            points.append(
                (
                    float(node["lat"]),
                    float(node["lon"])
                )
            )
        except (TypeError, KeyError, ValueError):
            return None

    if len(points) < 2:
        return None

    if not all(
        math.isfinite(value)
        for point in points
        for value in point
    ):
        return None

    try:
        aircraft_lat = float(aircraft.lat)
        aircraft_lon = float(aircraft.lon)
    except (TypeError, ValueError, AttributeError):
        return None

    if not math.isfinite(aircraft_lat) or not math.isfinite(aircraft_lon):
        return None

    cumulative = 0.0
    best = None

    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):

        segment = _distance_nm(
            lat1,
            lon1,
            lat2,
            lon2
        )

        if segment == 0:
            continue

        mean_lat = math.radians(
            (lat1 + lat2) / 2
        )

        dx = (
            math.radians(lon2 - lon1)
            * math.cos(mean_lat)
            * 3440.065
        )

        dy = (
            math.radians(lat2 - lat1)
            * 3440.065
        )

        px = (
            math.radians(aircraft_lon - lon1)
            * math.cos(mean_lat)
            * 3440.065
        )

        py = (
            math.radians(aircraft_lat - lat1)
            * 3440.065
        )

        denominator = dx * dx + dy * dy

        if denominator == 0:
            continue

        fraction = max(
            0.0,
            min(
                1.0,
                (px * dx + py * dy) / denominator
            )
        )

        cross_track = math.hypot(
            px - fraction * dx,
            py - fraction * dy
        )

        candidate = (
            cross_track,
            cumulative + fraction * segment
        )

        if best is None or candidate[0] < best[0]:
            best = candidate

        cumulative += segment

    return best[1] if best else None


def _route_distance_to_node(aircraft, graph, node_id):
    route = getattr(aircraft, "route", None) or []

    if node_id not in route:
        return None

    progress = arrival_path_progress(
        aircraft,
        graph
    )

    if progress is None:
        return None

    try:
        target_index = route.index(node_id)
    except ValueError:
        return None

    if target_index <= 0:
        return None

    total = 0.0

    for index in range(target_index):
        try:
            start = graph.get_node(route[index])
            end = graph.get_node(route[index + 1])

            total += _distance_nm(
                float(start["lat"]),
                float(start["lon"]),
                float(end["lat"]),
                float(end["lon"])
            )

        except (TypeError, KeyError, ValueError):
            return None

    remaining = total - progress

    if remaining < 0:
        return None

    return remaining


def _shared_waypoints(aircraft_list):
    waypoint_routes = {}

    for aircraft in aircraft_list:
        route = getattr(aircraft, "route", None) or []

        if len(route) < 2:
            continue

        route_id = getattr(
            aircraft,
            "arrival_route_id",
            None
        )

        if route_id is None:
            continue

        for node_id in route:
            waypoint_routes.setdefault(
                node_id,
                set()
            ).add(route_id)

    return {
        node_id
        for node_id, route_ids in waypoint_routes.items()
        if len(route_ids) > 1
    }


def _reset_separation_speed(aircraft):
    previous_target = getattr(
        aircraft,
        "_separation_target_speed",
        None
    )

    if previous_target is not None:

        if getattr(
            aircraft,
            "target_speed_kts",
            None
        ) == previous_target:

            aircraft.target_speed_kts = getattr(
                aircraft,
                "_separation_original_speed",
                aircraft.target_speed_kts
            )

        del aircraft._separation_target_speed

        if hasattr(
            aircraft,
            "_separation_original_speed"
        ):
            del aircraft._separation_original_speed


def _reset_shared_waypoint_speed(aircraft):
    previous_target = getattr(
        aircraft,
        "_shared_waypoint_target_speed",
        None
    )

    if previous_target is not None:

        if getattr(
            aircraft,
            "target_speed_kts",
            None
        ) == previous_target:

            aircraft.target_speed_kts = getattr(
                aircraft,
                "_shared_waypoint_original_speed",
                aircraft.target_speed_kts
            )

        del aircraft._shared_waypoint_target_speed

        if hasattr(
            aircraft,
            "_shared_waypoint_original_speed"
        ):
            del aircraft._shared_waypoint_original_speed


def apply_arrival_separation(aircraft_list, graph):
    arrivals_by_route = {}

    for aircraft in aircraft_list:

        _reset_separation_speed(aircraft)

        route_id = getattr(
            aircraft,
            "arrival_route_id",
            None
        )

        if (
            getattr(aircraft, "phase", "")
            not in ("ARRIVAL", "FINAL")
            or route_id is None
            or not getattr(aircraft, "route", None)
        ):
            continue

        progress = arrival_path_progress(
            aircraft,
            graph
        )

        if progress is not None:
            arrivals_by_route.setdefault(
                route_id,
                []
            ).append(
                (
                    progress,
                    aircraft
                )
            )

    for arrivals in arrivals_by_route.values():

        arrivals.sort(
            key=lambda item: (
                -item[0],
                item[1].callsign
            )
        )

        for index in range(1, len(arrivals)):

            leader_progress, leader = arrivals[index - 1]
            follower_progress, follower = arrivals[index]

            gap = (
                leader_progress
                - follower_progress
            )

            target = getattr(
                follower,
                "target_speed_kts",
                None
            )

            if (
                0 <= gap < MIN_ARRIVAL_SEPARATION_NM
                and target is not None
                and target >= MIN_ARRIVAL_SPEED_KTS
            ):

                adjusted = max(
                    MIN_ARRIVAL_SPEED_KTS,
                    leader.speed_kts
                    - (
                        MIN_ARRIVAL_SEPARATION_NM
                        - gap
                    ) * SEPARATION_SPEED_GAIN
                )

                follower._separation_original_speed = target

                follower.target_speed_kts = max(
                    MIN_ARRIVAL_SPEED_KTS,
                    min(
                        target,
                        adjusted
                    )
                )

                follower._separation_target_speed = (
                    follower.target_speed_kts
                )


def apply_shared_waypoint_separation(
    aircraft_list,
    graph
):
    for aircraft in aircraft_list:
        _reset_shared_waypoint_speed(
            aircraft
        )

    arrivals = [
        aircraft
        for aircraft in aircraft_list
        if (
            getattr(
                aircraft,
                "phase",
                ""
            ) in ("ARRIVAL", "FINAL")
            and getattr(
                aircraft,
                "arrival_route_id",
                None
            ) is not None
            and getattr(
                aircraft,
                "route",
                None
            )
        )
    ]

    if len(arrivals) < 2:
        return

    shared_waypoints = _shared_waypoints(
        arrivals
    )

    for waypoint_id in shared_waypoints:

        candidates = []

        for aircraft in arrivals:

            route_id = getattr(
                aircraft,
                "arrival_route_id",
                None
            )

            remaining = _route_distance_to_node(
                aircraft,
                graph,
                waypoint_id
            )

            speed = getattr(
                aircraft,
                "speed_kts",
                None
            )

            if (
                remaining is None
                or speed is None
                or speed <= 0
            ):
                continue

            route = getattr(
                aircraft,
                "route",
                None
            )

            try:
                waypoint_index = route.index(
                    waypoint_id
                )
            except ValueError:
                continue

            progress = arrival_path_progress(
                aircraft,
                graph
            )

            if progress is None:
                continue

            candidates.append(
                (
                    remaining / speed * 3600.0,
                    route_id,
                    waypoint_index,
                    aircraft
                )
            )

        if len(candidates) < 2:
            continue

        candidates.sort(
            key=lambda item: (
                item[0],
                item[3].callsign
            )
        )

        for index in range(1, len(candidates)):

            leader_eta = candidates[index - 1][0]
            follower_eta = candidates[index][0]
            follower = candidates[index][3]

            if (
                getattr(
                    follower,
                    "arrival_route_id",
                    None
                )
                == candidates[index - 1][1]
            ):
                continue

            required_eta = (
                leader_eta
                + MIN_SHARED_WAYPOINT_TIME_GAP
            )

            if follower_eta >= required_eta:
                continue

            remaining = _route_distance_to_node(
                follower,
                graph,
                waypoint_id
            )

            if remaining is None or remaining <= 0:
                continue

            target = getattr(
                follower,
                "target_speed_kts",
                None
            )

            if target is None:
                continue

            desired_time = (
                required_eta
            )

            if desired_time <= 0:
                continue

            required_speed = (
                remaining
                / desired_time
                * 3600.0
            )

            adjusted = max(
                MIN_ARRIVAL_SPEED_KTS,
                min(
                    target,
                    required_speed
                )
            )

            if adjusted >= target:
                continue

            follower._shared_waypoint_original_speed = target
            follower.target_speed_kts = adjusted
            follower._shared_waypoint_target_speed = adjusted


def update_trajectory(
    icao: str,
    altitude: float
):
    if altitude is None:
        return None

    key = f"trajectory:{icao}"
    now = time.time()

    raw = redis_client.get(key)

    if raw:
        history = deque(
            json.loads(raw),
            maxlen=MAX_POINTS
        )
    else:
        history = deque(
            maxlen=MAX_POINTS
        )

    history.append(
        (
            now,
            altitude
        )
    )

    redis_client.setex(
        key,
        WINDOW_SECONDS,
        json.dumps(
            list(history)
        )
    )

    return list(history)


def analyze_vertical_trend(history):
    if not history or len(history) < 3:
        return "UNKNOWN"

    try:
        t_start, alt_start = history[0]
        t_end, alt_end = history[-1]

        dt = t_end - t_start

        if dt <= 0:
            return "UNKNOWN"

        rate = (
            alt_end - alt_start
        ) / dt

    except Exception:
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