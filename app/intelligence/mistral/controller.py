from app.intelligence.mistral.client import generate
from app.intelligence.mistral.prompt import build_prompt
from app.intelligence.mistral.parser import parse
from app.intelligence.clearance_event_engine import clearance_event_engine
from app.intelligence.instruction_manager import instruction_manager
from app.intelligence.aircraft_state import ActiveClearance


class MistralController:

    def __init__(self):

        pass

    def update(
        self,
        aircraft,
        traffic
    ):

        event = clearance_event_engine.update(
            aircraft
        )

        if event is None:
            return

        aircraft.ai_busy = True

        try:

            prompt = build_prompt(
                aircraft,
                traffic,
                event
            )

            response = generate(
                prompt
            )

            data = parse(
                response
            )

            if data is None:

                aircraft.ai_busy = False

                return

            clearance = ActiveClearance(

                event=event,

                controller=data.get(
                    "controller",
                    "ATC"
                ),

                instruction=data.get(
                    "instruction",
                    ""
                ),

                heading=data.get(
                    "heading"
                ),

                altitude_ft=data.get(
                    "altitude"
                ),

                speed_kts=data.get(
                    "speed"
                ),

                next_node=data.get(
                    "next_node"
                ),

                runway=data.get(
                    "runway"
                ),

                completed=False

            )

            aircraft.assign_clearance(
                clearance
            )

            if data.get(
                "phase"
            ):

                aircraft.phase = data[
                    "phase"
                ]

            instruction_manager.issue(

                aircraft,

                clearance.controller,

                clearance.instruction

            )

            aircraft.pending_event = ""

        except Exception as e:

            print("MISTRAL ERROR:", e)

        finally:

            aircraft.ai_busy = False


mistral_controller = MistralController()