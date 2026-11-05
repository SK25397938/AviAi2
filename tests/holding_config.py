import json


def normalize_holding_routes(data):
    if isinstance(data, dict):
        data = data.get("holding_routes", [])
    if not isinstance(data, list):
        raise ValueError("holding_routes.json must contain an array or a holding_routes array")
    return data


def load_holding_routes(path):
    with path.open(encoding="utf-8") as source:
        return normalize_holding_routes(json.load(source))
