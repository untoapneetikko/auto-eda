import sys
import json
import pdfplumber


def extract(pdf_path: str) -> dict:
    text = ""
    tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
            tables.extend(page.extract_tables() or [])
    return {"text": text, "tables": tables, "source_pdf": pdf_path}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pdf_extractor.py <pdf_path>", file=sys.stderr)
        sys.exit(1)
    result = extract(sys.argv[1])
    print(json.dumps(result))
