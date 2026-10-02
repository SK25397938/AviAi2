import os
import json
from dotenv import load_dotenv
from app.intelligence.mistral.prompt import SYSTEM_PROMPT

try:
    from mistralai import Mistral
except ImportError:
    Mistral = None

load_dotenv()

MODEL = "ministral-3b-2512"

api_key = os.getenv(
    "MISTRAL_API_KEY"
)

client = (
    Mistral(api_key=api_key)
    if api_key and Mistral is not None
    else None
)

def _clean_response(content):
    if not isinstance(content, str):
        return None

    content = content.strip()

    if content.startswith("```"):
        lines = content.splitlines()

        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        content = "\n".join(lines).strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    required_fields = {
        "controller", "decision", "callsign", "instruction",
        "holding_route_id", "holding_fix", "altitude", "speed", "reason"
    }

    if not required_fields.issubset(data.keys()):
        return None

    return json.dumps(data)

def generate(
    prompt
):
    if client is None:
        print("MISTRAL ERROR: client is unavailable")
        return None

    try:
        response = client.chat.complete(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.1
        )

        content = response.choices[0].message.content

        cleaned = _clean_response(content)

        print("\n========== MISTRAL RESPONSE ==========")
        print(cleaned if cleaned is not None else content)
        print("======================================\n")

        return cleaned

    except Exception as e:
        print("\n========== MISTRAL ERROR ==========")
        print(e)
        print("===================================\n")
        return None
