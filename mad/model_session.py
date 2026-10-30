"""Async model invocation and structured-response retries."""
import asyncio
import json
from mad.protocol import parse_json_response


class ModelSession:
    def __init__(self, agent, reporter):
        self.agent = agent
        self.reporter = reporter

    async def _chat(
        self,
        messages,
        max_tokens=4096,
        json_schema=None,
        retries=2,
    ):
        last_error = None

        for attempt in range(
            retries + 1
        ):
            try:
                return await asyncio.to_thread(
                    self.agent.server.chat,
                    model=self.agent.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    json_schema=json_schema,
                )

            except Exception as error:
                last_error = error

                if attempt >= retries:
                    raise

                self.reporter.print(
                    f"[yellow]"
                    f"⚠ {self.agent.name} model request failed "
                    f"(attempt {attempt + 1}/{retries + 1}): "
                    f"{error}"
                    f"[/yellow]"
                )

                await asyncio.sleep(
                    1.0
                )

        raise last_error

    async def structured_chat(
        self,
        messages,
        schema,
        max_tokens=4096,
        retries=2,
    ):
        conversation = list(
            messages
        )

        for attempt in range(
            retries + 1
        ):
            response = await self._chat(
                conversation,
                max_tokens=max_tokens,
                json_schema=schema,
            )

            try:
                return (
                    parse_json_response(
                        response
                    ),
                    response,
                )

            except json.JSONDecodeError as error:
                self.reporter.print(
                    f"[yellow]"
                    f"⚠ {self.agent.name} returned "
                    f"invalid JSON "
                    f"(attempt {attempt + 1}/"
                    f"{retries + 1}): "
                    f"{error}"
                    f"[/yellow]"
                )

                self.reporter.print(
                    f"[dim]"
                    f"Raw response: "
                    f"{response[:1000]}"
                    f"[/dim]"
                )

                if attempt >= retries:
                    raise

                conversation.append(
                    {
                        "role": "assistant",
                        "content": response,
                    }
                )

                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was invalid "
                            "or incomplete JSON. Return one complete "
                            "valid JSON object matching the schema."
                        ),
                    }
                )

        raise RuntimeError(
            "Structured response failed."
        )
