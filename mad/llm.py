import re
import requests


class LLMClient:
    def __init__(self, base_url="http://localhost:8000/v1"):
        self.base_url = base_url.rstrip("/")

    def chat(self, model, messages, max_tokens=1024, temperature=0.2):
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=300,
        )

        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]

        # Remove model reasoning blocks such as <think>...</think>
        content = re.sub(
            r"<think>.*?</think>",
            "",
            content,
            flags=re.DOTALL
        ).strip()

        return content
