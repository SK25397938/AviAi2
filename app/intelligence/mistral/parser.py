import json


REQUIRED_FIELDS = {
    "controller", "decision", "callsign", "instruction",
    "holding_route_id", "holding_fix", "altitude", "speed",
    "hold_circuits", "rejoin_node", "reason"
}


def parse(response):
    if not isinstance(response, str):
        return None
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or set(data) != REQUIRED_FIELDS:
        return None
    if data.get("controller") != "Arrival" or data.get("decision") not in {"HOLD", "NONE"}:
        return None
    if not isinstance(data.get("instruction"), str) or not isinstance(data.get("reason"), str):
        return None
    if data["decision"] == "NONE":
        if any(data.get(key) is not None for key in (
            "callsign", "holding_route_id", "holding_fix", "altitude", "speed", "hold_circuits", "rejoin_node"
        )):
            return None
        if data["instruction"] or data["reason"]:
            return None
        return data
    if any(not isinstance(data.get(key), str) or not data[key].strip()
           for key in ("callsign", "holding_route_id", "holding_fix", "instruction")):
        return None
    for key, low, high in (("altitude", 0, 60000), ("speed", 0, 500)):
        value = data.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not low < value <= high):
            return None
    circuits = data.get("hold_circuits")
    if isinstance(circuits, bool) or not isinstance(circuits, int) or not 1 <= circuits <= 10:
        return None
    if data.get("rejoin_node") is not None and not isinstance(data["rejoin_node"], str):
        return None
    return data
