import json
import csv
import logging
import re
from pathlib import Path


BASE = Path("data/airports/VABB")

ROUTES_FILE = (
    Path(__file__).resolve().parent
    / "routes"
    / "vabb_routes.json"
)

ARRIVAL_PROFILES_FILE = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "vabb_arrival_profiles.json"
)
STAR_PROFILE_CSV = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "vabb_rwy27_star_profiles.csv"
)
DEPARTURE_PROFILE_CSV = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "rwy27_departure_speed_height_profiles_realistic.csv"
)
DEPARTURE_DESTINATION_MAP_FILE = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "vabb_destination_sids.json"
)

DEPARTURE_ROUTES_FILE = (
    Path(__file__).resolve().parents[2]
    / "vabb_departure.json"
)
logger = logging.getLogger(__name__)


def load_airport():

    with open(
        BASE / "airport.json",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def load_runways():

    with open(
        BASE / "runways.json",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def load_navigation():

    with open(
        BASE / "navigation.json",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def load_arrival_routes():

    with open(
        ROUTES_FILE,
        encoding="utf-8"
    ) as file:

        routes = json.load(file)["routes"]

    return {
        route["id"]: route
        for route in routes
    }


def load_arrival_profiles():
    """Load route/waypoint constraints from the supplied STAR profile CSV."""
    profiles = {}
    try:
        with STAR_PROFILE_CSV.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                route_id = str(row.get("route_id", "")).strip()
                waypoint = str(row.get("waypoint", "")).strip().upper()
                if not route_id or not waypoint:
                    continue
                profile = {"source": "vabb_rwy27_star_profiles.csv",
                           "cum_nm": _float_or_none(row.get("cum_nm")),
                           "profile_altitude_ft": _float_or_none(row.get("alt_ft")),
                           "alt_basis": str(row.get("alt_basis", "")).strip(),
                           "spd_basis": str(row.get("spd_basis", "")).strip()}
                altitude = _float_or_none(row.get("alt_ft"))
                alt_basis = profile["alt_basis"].lower()
                rule = str(row.get("alt_rule", "")).strip().lower()
                if altitude is not None and alt_basis == "published":
                    kind = {"at": "AT", "at or above": "AT_OR_ABOVE",
                            "at or below": "AT_OR_BELOW"}.get(rule)
                    notation = str(row.get("alt_pub", "")).strip()
                    notation_kind = ("AT" if notation.startswith("@") else
                                     "AT_OR_ABOVE" if notation.startswith("+") else
                                     "AT_OR_BELOW" if notation.startswith("-") else None)
                    if kind and kind == notation_kind:
                        profile["altitude"] = {"type": kind, "feet": altitude}
                    else:
                        logger.warning("Unresolved published altitude route=%s waypoint=%s notation=%r rule=%r basis=%r",
                                       route_id, waypoint, notation, rule, alt_basis)
                speed = _float_or_none(row.get("spd_kt"))
                speed_basis = profile["spd_basis"].lower()
                speed_rule = str(row.get("spd_rule", "")).strip().lower()
                if speed is not None and speed_basis not in {"no published limit", ""}:
                    kind = {"at or below": "MAX", "at": "AT", "": "MAX"}.get(speed_rule)
                    notation = str(row.get("spd_pub", "")).strip()
                    notation_kind = ("AT" if notation.startswith("@") else
                                     "MAX" if notation.startswith("-") else
                                     "MAX" if speed_basis != "published" and not notation else None)
                    if kind and (speed_basis != "published" or kind == notation_kind):
                        profile["speed"] = {"type": kind, "knots": speed,
                                             "basis": "published" if speed_basis == "published" else "carried"}
                    else:
                        logger.warning("Unresolved speed profile route=%s waypoint=%s notation=%r rule=%r basis=%r",
                                       route_id, waypoint, notation, speed_rule, speed_basis)
                profiles.setdefault(route_id, {})[waypoint] = profile
    except (OSError, csv.Error):
        profiles = {}
    if profiles:
        return profiles
    """Backward-compatible config fallback if the shipped CSV is unavailable."""
    try:
        with ARRIVAL_PROFILES_FILE.open(encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    routes = payload.get("routes", {}) if isinstance(payload, dict) else {}
    if not isinstance(routes, dict):
        return {}
    normalized = {}
    for route_id, waypoints in routes.items():
        if not isinstance(waypoints, dict):
            continue
        normalized[str(route_id)] = {
            str(name).upper(): profile
            for name, profile in waypoints.items()
            if isinstance(profile, dict)
        }
    return normalized


def _float_or_none(value):
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def load_departure_routes():

    with open(
        DEPARTURE_ROUTES_FILE,
        encoding="utf-8"
    ) as file:

        routes = json.load(file)

    return {
        route["route_id"]: route
        for route in routes.values()
    }


def load_departure_destination_map():
    """Load the explicit destination-to-SID map used by scheduled departures."""
    try:
        with DEPARTURE_DESTINATION_MAP_FILE.open(encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    mapping = payload.get("destinations", {}) if isinstance(payload, dict) else {}
    if not isinstance(mapping, dict):
        return {}
    return {str(destination).strip().upper(): str(route_id).strip()
            for destination, route_id in mapping.items()
            if str(destination).strip() and str(route_id).strip()}


def load_departure_profiles():
    """Load route, aircraft, and waypoint matched RWY27 departure profiles."""
    profiles = {}
    try:
        with DEPARTURE_PROFILE_CSV.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                route_id = str(row.get("route", "")).strip()
                route_name = str(row.get("route_name", "")).strip().upper()
                aircraft = str(row.get("aircraft", "")).strip().upper()
                waypoint = str(row.get("waypoint", "")).strip().upper()
                if not all((route_id, route_name, aircraft, waypoint)):
                    continue
                profile = dict(row)
                for field in ("seq", "lat", "lon", "leg_nm", "dist_from_runway_nm",
                              "alt_ft", "ias_kt", "gs_kt", "climb_ft_per_nm", "vs_fpm"):
                    profile[field] = _float_or_none(row.get(field))
                profile["pub_alt_constraint"] = _parse_published_constraint(row.get("pub_alt"), "ft")
                profile["pub_speed_constraint"] = _parse_published_constraint(row.get("pub_spd"), "kt")
                profiles.setdefault((route_id, route_name, aircraft), []).append(profile)
    except (OSError, csv.Error):
        logger.exception("Unable to load RWY27 departure profiles from %s", DEPARTURE_PROFILE_CSV)
        return {}
    for rows in profiles.values():
        rows.sort(key=lambda item: item.get("seq") if item.get("seq") is not None else float("inf"))
    return profiles


def _parse_published_constraint(value, default_unit):
    text = str(value or "").strip().replace(",", "")
    match = re.fullmatch(r"(>=|<=|>|<|=|@)?\s*(-?\d+(?:\.\d+)?)\s*(ft|feet|kt|kts|knots)?", text, re.IGNORECASE)
    if not match:
        return None
    operator = match.group(1) or "="
    unit = (match.group(3) or default_unit).lower()
    return {"operator": operator, "value": float(match.group(2)), "unit": unit}
