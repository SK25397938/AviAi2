def classify_aircraft(callsign: str | None) -> str:
    if not callsign:
        return "UNKNOWN"

    cs = callsign.lower()

    # Widebody operators
    if cs.startswith(("aic", "qtr", "uae", "eth", "kal", "ana")):
        return "WIDEBODY"

    # Narrowbody operators
    if cs.startswith(("ind", "igo", "ryr", "ezy", "wzz", "aal", "dal")):
        return "NARROWBODY"

    # Regional aircraft
    if cs.startswith(("atr", "jza", "glo")):
        return "REGIONAL"

    return "UNKNOWN"
