import asyncio
import sys

sys.path.insert(0, ".")

from app.services.file_parser import FileParserFactory
from app.services.autofill.llm_word_form_service import enhance_word_template_fields

PATH = "uploads/1014_1779189892.498525_demoDonXinViec.docx"


def main() -> None:
    parser = FileParserFactory.create_parser(PATH)
    baseline = parser.parse()
    print("=== BASELINE", len(baseline), "===")
    for f in baseline:
        print(f"  [{f.section}] {f.name} | {f.label}")

    async def run() -> None:
        fields, meta = await enhance_word_template_fields(
            file_path=PATH,
            parser_fields=baseline,
            original_filename="demoDonXinViec.docx",
        )
        print("=== ENHANCED", len(fields), meta.get("strategy"), "===")
        sections: dict[str, list] = {}
        for f in fields:
            sec = f.section or "NONE"
            sections.setdefault(sec, []).append(f)
        for sec, flist in sections.items():
            print(f"SECTION: {sec!r} ({len(flist)})")
            for f in flist:
                print(f"  {f.name} | {f.label} | {f.field_type}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
