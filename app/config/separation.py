"""Shared aircraft separation minima."""

MIN_HORIZONTAL_SEPARATION_NM = 6.0


def satisfies_horizontal_separation(distance_nm):
    """Whether a horizontal distance meets the shared minimum exactly."""
    try:
        return float(distance_nm) >= MIN_HORIZONTAL_SEPARATION_NM
    except (TypeError, ValueError):
        return False
