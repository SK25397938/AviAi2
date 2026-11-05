"""Global, configured VABB approach delay fixes."""

GLOBAL_ARRIVAL_FALLBACKS = ("OLGUS", "MB398")


def compatible_fallbacks(preferences, route, route_index, graph_nodes):
    if (not isinstance(route_index, int) or isinstance(route_index, bool)
            or route_index < -1 or route_index > len(route or [])):
        return []
    available = {str(name).strip().upper() for name in graph_nodes}
    # Route preferences can rank the global candidates, but STAR membership
    # is not a requirement and cannot introduce a third fallback.
    configured = [str(name).strip().upper() for name in (preferences or [])
                  if isinstance(name, str)
                  and str(name).strip().upper() in GLOBAL_ARRIVAL_FALLBACKS]
    order = configured + [name for name in GLOBAL_ARRIVAL_FALLBACKS if name not in configured]
    return [name for name in order if name in available]


def evaluate_fallback_candidates(preferences, route, route_index, graph_nodes):
    candidates = compatible_fallbacks(preferences, route, route_index, graph_nodes)
    if not candidates:
        return {"status": "NO_COMPATIBLE_FALLBACK", "candidates": []}
    return {"status": "CANDIDATES_AVAILABLE", "candidates": candidates}


def route_end_fallback_candidates(preferences, route, route_index, trigger, graph_nodes):
    """Return global fallbacks for a completed route ending at its trigger."""
    if (not isinstance(route, list) or not route
            or not isinstance(route_index, int) or isinstance(route_index, bool)
            or route_index != len(route)
            or not isinstance(trigger, str) or not trigger
            or str(route[-1]).strip().upper() != trigger.strip().upper()):
        return []
    return compatible_fallbacks(preferences, route, route_index, graph_nodes)
