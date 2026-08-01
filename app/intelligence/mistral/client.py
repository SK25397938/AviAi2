import os

from dotenv import load_dotenv

from mistralai import Mistral


load_dotenv()


MODEL = "mistral-small-latest"


api_key = os.getenv(

    "MISTRAL_API_KEY"

)

if not api_key:

    raise RuntimeError(

        "MISTRAL_API_KEY not found in .env"

    )


client = Mistral(

    api_key=api_key

)


def generate(

    prompt

):

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