import json

SYSTEM_PROMPT = """
You are AviAi.

You are the sole Air Traffic Controller for VABB Mumbai.

Your job is to issue exactly ONE clearance whenever a new ATC event occurs.

You receive one aircraft state and nearby traffic.

Possible events:

SPAWN
WAYPOINT_REACHED
HEADING_REACHED
ALTITUDE_REACHED
SPEED_REACHED
LOCALIZER_CAPTURED
GLIDESLOPE_CAPTURED
LANDING_CLEARANCE
RUNWAY_EXIT
TAXI
PARKED

Rules:

- Think exactly like a real Arrival, Approach or Tower controller.
- Maintain safe separation.
- Vector aircraft toward Runway 27.
- Descend aircraft gradually.
- Reduce speed realistically.
- Capture the localizer.
- Capture the glideslope.
- Issue landing clearance only on final.
- After landing issue runway exit instructions.
- Never explain your reasoning.
- Return ONE JSON object only.
- Never return markdown.
- Never return code blocks.
- Never return text outside the JSON.

JSON FORMAT

{
    "controller":"Arrival",
    "instruction":"Turn left heading 270, descend 5000 feet.",
    "heading":270,
    "altitude":5000,
    "speed":210,
    "runway":"27",
    "next_node":"N12",
    "phase":"ARRIVAL",
    "reason":""
}
"""
def build_prompt(

    aircraft,

    traffic,

    event

):

    nearby = []

    for ac in traffic:

        if ac.callsign == aircraft.callsign:

            continue

        nearby.append({

            "callsign": ac.callsign,

            "lat": round(ac.lat,6),

            "lon": round(ac.lon,6),

            "altitude": ac.altitude_ft,

            "speed": ac.speed_kts,

            "heading": round(ac.heading_deg),

            "phase": ac.phase

        })

    state = {

        "event": event,

        "airport": "VABB",

        "active_runway": "27",

        "callsign": aircraft.callsign,

        "aircraft": aircraft.aircraft_type,

        "position": {

            "lat": round(aircraft.lat,6),

            "lon": round(aircraft.lon,6)

        },

        "heading": round(

            aircraft.heading_deg

        ),

        "speed": aircraft.speed_kts,

        "altitude": aircraft.altitude_ft,

        "vertical_speed": aircraft.vertical_speed_fpm,

        "phase": aircraft.phase,

        "approach_phase": aircraft.approach_phase,

        "assigned_node": aircraft.assigned_node,

        "target_node": aircraft.target_node,

        "runway": aircraft.clearance.runway,

        "on_ground": aircraft.on_ground,

        "traffic": nearby

    }

    return (

        SYSTEM_PROMPT

        +

        "\n\n"

        +

        json.dumps(

            state,

            indent=2

        )

    )