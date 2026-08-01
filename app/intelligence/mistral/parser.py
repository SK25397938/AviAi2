import json


def parse(
    response
):

    if response is None:
        return None

    response = response.strip()

    if response.startswith("```"):

        response = (
            response
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

    try:

        return json.loads(response)

    except Exception:

        print("INVALID MISTRAL RESPONSE")

        print(response)

        return None