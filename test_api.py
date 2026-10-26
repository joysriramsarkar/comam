"""Connectivity + Bengali test for the Meta Model API (Standard model).

Run:
    pip install openai
    # PowerShell:  $env:MODEL_API_KEY = "your-key"
    python test_api.py

Success = a Bengali answer printed below. This only proves the API works;
it is not the app.
"""
import os

from openai import OpenAI

api_key = os.getenv("MODEL_API_KEY")
if not api_key:
    raise RuntimeError("MODEL_API_KEY is not set")

client = OpenAI(base_url="https://api.meta.ai/v1", api_key=api_key)

response = client.chat.completions.create(
    model="muse-spark-1.3",  # Standard tier — the runtime model. Never -contributor in the app.
    messages=[
        {
            "role": "user",
            "content": (
                "বাংলায় উত্তর দাও: comam AI কীভাবে ছোট দোকানের "
                "বিক্রির হিসাব রাখতে সাহায্য করবে? দুই লাইনে বলো।"
            ),
        }
    ],
)

print(response.choices[0].message.content)
if getattr(response, "usage", None):
    print("\n[tokens]", response.usage)
