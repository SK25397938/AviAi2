from datetime import datetime


class InstructionManager:

    def __init__(self):

        self.logs = []

    def issue(

        self,

        aircraft,

        controller,

        instruction

    ):

        if instruction is None:

            return

        instruction = instruction.strip()

        if instruction == "":

            return

        if (

            aircraft.controller == controller

            and

            aircraft.last_instruction == instruction

        ):

            return

        aircraft.controller = controller

        aircraft.last_instruction = instruction

        self.logs.append({

            "time": datetime.now().strftime(

                "%H:%M:%S"

            ),

            "callsign": aircraft.callsign,

            "controller": controller,

            "instruction": instruction

        })

        if len(self.logs) > 500:

            self.logs.pop(0)

    def latest(

        self

    ):

        return self.logs


instruction_manager = InstructionManager()