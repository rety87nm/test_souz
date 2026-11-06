from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app import config

ChatMessage = SystemMessage | HumanMessage | AIMessage | ToolMessage


class LLMError(RuntimeError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMResponseError(LLMError):
    pass


class LLMClient:
    def __init__(self) -> None:
        self._llm = ChatOpenAI(
            model=config.MODEL,
            base_url=config.BASE_URL,
            api_key=config.API_KEY or "not-needed",
            temperature=config.TEMPERATURE,
            max_tokens=config.MAX_TOKENS,
            timeout=config.TIMEOUT,
            max_retries=0,
            model_kwargs={"response_format": {"type": "json_object"}},
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

    async def close(self) -> None:
        await self._llm.root_async_client.close()

    async def health(self) -> dict[str, Any]:
        try:
            page = await self._llm.root_async_client.models.list()
        except APITimeoutError as exc:
            raise LLMTimeoutError("LLM не ответила вовремя") from exc
        except APIConnectionError as exc:
            raise LLMError(f"Не удалось подключиться к LLM: {exc}") from exc
        except APIStatusError as exc:
            raise LLMError(f"LLM вернула HTTP {exc.status_code}: {exc}") from exc

        return {"data": [{"id": model.id} for model in page.data]}

    async def chat_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        messages: list[ChatMessage] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        last_error: Exception | None = None
        attempts = config.MAX_RETRIES + 1

        for attempt in range(1, attempts + 1):
            try:
                response = await self._llm.ainvoke(messages)
                return self._parse_json(response.text)
            except APITimeoutError as exc:
                last_error = LLMTimeoutError("LLM не ответила вовремя")
            except APIStatusError as exc:
                if exc.status_code == 429 or exc.status_code >= 500:
                    last_error = LLMError(f"LLM вернула HTTP {exc.status_code}")
                else:
                    raise LLMError(
                        f"LLM отклонила запрос (HTTP {exc.status_code}): {str(exc)[:500]}"
                    ) from exc
            except APIConnectionError as exc:
                last_error = LLMError(f"Ошибка сети при обращении к LLM: {exc}")
            except LLMResponseError as exc:
                last_error = exc

            if attempt < attempts:
                await asyncio.sleep(1.5 * attempt)

        raise last_error or LLMError("Не удалось получить ответ от LLM")

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("LLM вернула пустой ответ")

        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise LLMResponseError("LLM вернула ответ не в формате JSON")
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise LLMResponseError("LLM вернула некорректный JSON") from exc

        if not isinstance(parsed, dict):
            raise LLMResponseError("LLM вернула JSON не в виде объекта")
        return parsed
