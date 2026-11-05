import math


def mach_to_true_airspeed(mach, altitude_ft):
    altitude_m = max(0.0, float(altitude_ft)) * 0.3048
    temperature = max(216.65, 288.15 - 0.0065 * altitude_m)
    return 661.4786 * math.sqrt(temperature / 288.15) * float(mach)


def indicated_to_true_airspeed(ias_kts, altitude_ft):
    """Convert indicated knots to true airspeed, used as still-air groundspeed."""
    altitude_m = max(0.0, float(altitude_ft)) * 0.3048
    if altitude_m <= 11000.0:
        temperature = 288.15 - 0.0065 * altitude_m
        pressure_ratio = (temperature / 288.15) ** 5.2558797
    else:
        temperature = 216.65
        pressure_ratio = 0.2233611 * math.exp(
            -9.80665 * (altitude_m - 11000.0) / (287.05287 * temperature)
        )
    speed_of_sound = 661.4786 * math.sqrt(temperature / 288.15)
    sea_level_pressure = 101325.0
    impact_pressure = sea_level_pressure * (
        (1.0 + 0.2 * (float(ias_kts) / 661.4786) ** 2) ** 3.5 - 1.0
    )
    mach = math.sqrt(5.0 * (
        (impact_pressure / (sea_level_pressure * pressure_ratio) + 1.0) ** (2.0 / 7.0) - 1.0
    ))
    return mach * speed_of_sound


def true_to_indicated_airspeed(true_airspeed_kts, altitude_ft):
    altitude_m = max(0.0, float(altitude_ft)) * 0.3048
    if altitude_m <= 11000.0:
        temperature = 288.15 - 0.0065 * altitude_m
        pressure_ratio = (temperature / 288.15) ** 5.2558797
    else:
        temperature = 216.65
        pressure_ratio = 0.2233611 * math.exp(
            -9.80665 * (altitude_m - 11000.0) / (287.05287 * temperature)
        )
    speed_of_sound = 661.4786 * math.sqrt(temperature / 288.15)
    mach = max(0.0, float(true_airspeed_kts)) / speed_of_sound
    sea_level_pressure = 101325.0
    impact_pressure = sea_level_pressure * pressure_ratio * (
        (1.0 + 0.2 * mach ** 2) ** 3.5 - 1.0
    )
    return 661.4786 * math.sqrt(5.0 * (
        (impact_pressure / sea_level_pressure + 1.0) ** (2.0 / 7.0) - 1.0
    ))
