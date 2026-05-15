from __future__ import annotations

import os
from typing import Any

from app.core.logger import logger
from app.services.autofill.contracts import CanonicalFormField, CanonicalFormSchema
from app.services.autofill.llm_client import LLMClient
from app.services.file_parser import FileParserFactory


class LLMFormParseAgent:
    """
    Parse uploaded form and produce canonical schema.
    Uses parser-first for stability, LLM for optional structure enrichment.
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def parse_uploaded_file(
        self,
        *,
        file_path: str,
        source_type: str,
        source_ref: str,
        original_filename: str,
    ) -> CanonicalFormSchema:
        parser = FileParserFactory.create_parser(file_path)
        parsed_fields = parser.parse()
        metadata = parser.get_metadata() or {}

        fields: list[CanonicalFormField] = []
        for idx, parsed in enumerate(parsed_fields):
            try:
                payload = parsed.to_dict()
            except Exception:
                payload = {}
            name = str(payload.get("name") or f"field_{idx + 1}").strip().lower().replace(" ", "_")
            label = str(payload.get("label") or name).strip()
            field_type = str(payload.get("field_type") or "text").strip() or "text"
            fields.append(
                CanonicalFormField(
                    field_key=name,
                    label=label,
                    field_type=field_type,
                    required=bool(payload.get("required", False)),
                    group=str(payload.get("group") or "general"),
                    aliases=[],
                    constraints={},
                    raw_context={"order": payload.get("order", idx)},
                )
            )

        # LLM enrichment is best-effort; keep parser output as baseline.
        if fields:
            llm_prompt = (
                "Normalize form fields into JSON with keys: "
                "field_key,label,field_type,required,group,aliases,constraints. "
                f"filename={original_filename}, fields={[(f.field_key, f.label, f.field_type) for f in fields]}"
            )
            llm_result = await self._llm.complete_json(
                task_name="form_parse_enrichment",
                prompt=llm_prompt,
                fallback={},
            )
            enriched = llm_result.get("fields") if isinstance(llm_result, dict) else None
            if isinstance(enriched, list):
                rewritten: list[CanonicalFormField] = []
                for idx, item in enumerate(enriched):
                    if not isinstance(item, dict):
                        continue
                    base_key = str(item.get("field_key") or f"field_{idx + 1}").strip().lower().replace(" ", "_")
                    rewritten.append(
                        CanonicalFormField(
                            field_key=base_key,
                            label=str(item.get("label") or base_key).strip(),
                            field_type=str(item.get("field_type") or "text").strip() or "text",
                            required=bool(item.get("required", False)),
                            group=str(item.get("group") or "general"),
                            aliases=[str(v).strip() for v in (item.get("aliases") or []) if str(v).strip()],
                            constraints=item.get("constraints") if isinstance(item.get("constraints"), dict) else {},
                            raw_context={"source": "llm"},
                        )
                    )
                if rewritten:
                    fields = rewritten

        filename = os.path.basename(original_filename or file_path)
        logger.info(f"LLMFormParseAgent parsed {len(fields)} fields from {filename}")

        return CanonicalFormSchema(
            source_type=source_type,
            source_ref=source_ref,
            filename=filename,
            fields=fields,
            metadata=metadata if isinstance(metadata, dict) else {},
        )

