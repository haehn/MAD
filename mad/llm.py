import re
import requests


class LLM:
    def __init__(
        self,
        base_url="http://localhost:8000/v1",
        show_thinking=False,
    ):
        self.base_url = base_url.rstrip(
            "/"
        )

        self.show_thinking = (
            show_thinking
        )

    def chat(
        self,
        model,
        messages,
        max_tokens=2048,
        temperature=0.2,
        json_schema=None,
    ):
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if json_schema is not None:
            payload[
                "response_format"
            ] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "mad_response",
                    "schema": json_schema,
                    "strict": True,
                },
            }

        response = requests.post(
            (
                f"{self.base_url}"
                f"/chat/completions"
            ),
            headers={
                "Content-Type": (
                    "application/json"
                )
            },
            json=payload,
            timeout=300,
        )

        if not response.ok:
            try:
                detail = response.json()
            except Exception:
                detail = response.text

            raise RuntimeError(
                f"vLLM request failed "
                f"({response.status_code}): "
                f"{detail}"
            )

        data = response.json()

        content = (
            data["choices"][0]
            ["message"]
            .get(
                "content",
                "",
            )
            or ""
        )

        if not self.show_thinking:
            content = re.sub(
                r"<think>.*?</think>",
                "",
                content,
                flags=re.DOTALL,
            ).strip()

        return content
