from __future__ import annotations

from app.core.logger import logger
from app.services.autofill.contracts import CanonicalFormSchema
from app.services.autofill.llm_client import LLMClient


class LLMFieldUnderstandingAgent:
    """Resolve field aliases, normalize semantics, and infer constraints."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def understand(self, schema: CanonicalFormSchema) -> CanonicalFormSchema:
        if not schema.fields:
            return schema

        compact_fields = [
            {
                "field_key": field.field_key,
                "label": field.label,
                "field_type": field.field_type,
                "aliases": field.aliases,
            }
            for field in schema.fields
        ]
        prompt = (
            "Given canonical form fields, infer better aliases and basic constraints. "
            "Return JSON {fields:[{field_key,aliases,constraints,field_type}]} only. "
            f"fields={compact_fields}"
        )
        llm_result = await self._llm.complete_json(
            task_name="field_understanding",
            prompt=prompt,
            fallback={},
        )
        updates = llm_result.get("fields") if isinstance(llm_result, dict) else None
        if not isinstance(updates, list):
            return schema

        update_map: dict[str, dict] = {}
        for item in updates:
            if not isinstance(item, dict):
                continue
            key = str(item.get("field_key") or "").strip()
            if not key:
                continue
            update_map[key] = item

        for field in schema.fields:
            item = update_map.get(field.field_key)
            if not item:
                continue
            aliases = item.get("aliases")
            constraints = item.get("constraints")
            inferred_type = str(item.get("field_type") or "").strip()
            if isinstance(aliases, list):
                normalized = [str(v).strip() for v in aliases if str(v).strip()]
                if normalized:
                    field.aliases = sorted(set(field.aliases + normalized))
            if isinstance(constraints, dict):
                field.constraints.update(constraints)
            if inferred_type:
                field.field_type = inferred_type

        logger.info(f"LLMFieldUnderstandingAgent enriched {len(schema.fields)} fields")
        return schema

