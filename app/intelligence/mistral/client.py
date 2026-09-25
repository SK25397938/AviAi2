import os

from dotenv import load_dotenv

try:

    from mistralai import Mistral

except ImportError:

    Mistral = None


load_dotenv()


MODEL = "mistral-small-latest"


api_key = os.getenv(

    "MISTRAL_API_KEY"

)

client = (
    Mistral(api_key=api_key)
    if api_key and Mistral is not None
    else None
)


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

                    "content": "You are AviAi, a professional Air Traffic Controller. Respond with ONLY valid JSON."

                },

                {

                    "role": "user",

                    "content": prompt

                }

            ],

            temperature=0.2

        )

        content = response.choices[0].message.content

        print("\n========== MISTRAL RESPONSE ==========")

        print(content)

        print("======================================\n")

        return content

    except Exception as e:

        print("\n========== MISTRAL ERROR ==========")

        print(e)

        print("===================================\n")

        return None
