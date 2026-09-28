"""Page-by-page text extraction via pypdf. Free, deterministic, no LLM —
same shape as the prior TP-review project's extract_pdf_text.
"""
from pypdf import PdfReader


def extract_pdf_text(pdf_path: str) -> list[dict]:
    """Returns one dict per page, in page order:
    {"page_number": int, "text": str, "low_text": bool}.

    low_text flags a page whose extracted text is suspiciously short (e.g. a
    scanned image with no text layer) — a genuine "couldn't read this page"
    signal for downstream classification to route into `unresolved` rather
    than silently treating an empty page as a real, resolvable one.
    """
    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        pages.append({
            "page_number": i + 1,
            "text": text,
            "low_text": len(text.strip()) < 20,
        })
    return pages
