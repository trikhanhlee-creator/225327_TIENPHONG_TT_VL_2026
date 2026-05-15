from __future__ import annotations

import json
import re
from typing import Any

from app.core.logger import logger
from app.services.ai_composer_service import AIComposerService


class LLMClient:
    """Thin wrapper around existing AI provider stack for agent JSON tasks."""

    def __init__(self) -> None:
        self._composer = AIComposerService()

    async def complete_json(
        self,
        task_name: str,
        prompt: str,
        fallback: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """
        Execute a JSON-oriented LLM task.
        Reuses composer service to avoid duplicating provider/failover code.
        """
        try:
            suggestions = await self._composer.get_text_suggestions(
                context=prompt,
                max_suggestions=1,
                suggestion_length=80,
                mode="rewrite",
                original_text=prompt,
                instruction=f"Return strict JSON for task: {task_name}",
            )
            text = ""
            if suggestions:
                text = str((suggestions[0] or {}).get("text", "")).strip()
            parsed = self._extract_json(text)
            if parsed is not None:
                return parsed
        except Exception as exc:
            logger.warning(f"LLMClient complete_json failed [{task_name}]: {exc}")

        if fallback is not None:
            return fallback
        return {}

    def _extract_json(self, raw: str) -> dict[str, Any] | list[Any] | None:
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                return json.loads(text)
            except Exception:
                pass
        if text.startswith("[") and text.endswith("]"):
            try:
                return json.loads(text)
            except Exception:
                pass

        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if not match:
            return None

        try:
            return json.loads(match.group(1))
        except Exception:
            return None

