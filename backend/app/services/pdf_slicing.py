import tempfile

from pypdf import PdfReader, PdfWriter


def slice_pdf_to_temp_file(src_path: str, page_start: int, page_end: int) -> str:
    """Extracts one PersonDocument's own page range (1-indexed, inclusive)
    out of a batch's original PDF into a standalone temp file —
    review_person_document operates on ONE document at a time, per Phase 1's
    classify_person_docs.py. Caller is responsible for deleting the
    returned path once done with it.
    """
    reader = PdfReader(src_path)
    writer = PdfWriter()
    for i in range(page_start - 1, page_end):
        writer.add_page(reader.pages[i])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer.write(tmp)
    tmp.close()
    return tmp.name
