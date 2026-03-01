from pathlib import Path

PDF_PATH = Path(r"c:\HackFusion3_Project\HackfusionPS.pdf")
OUT_PATH = Path("docs") / "HackfusionPS_extracted.txt"


def extract_with_pypdf(pdf_path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        parts.append(f"\n\n===== PAGE {i} =====\n{text}")
    return "".join(parts)


def main():
    if not PDF_PATH.exists():
        raise FileNotFoundError(f"PDF not found: {PDF_PATH}")

    try:
        text = extract_with_pypdf(PDF_PATH)
    except Exception as e:
        print("Could not extract PDF text with pypdf.")
        print("Install dependency and retry:")
        print("  pip install pypdf")
        print(f"Error: {e}")
        return

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(text, encoding="utf-8")
    print(f"Extracted text written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
