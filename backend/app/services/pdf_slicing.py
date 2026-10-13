import tempfile

from pypdf import PdfReader, PdfWriter


def slice_pdf_to_temp_file(src_path: str, page_start: int, page_end: int, *, reader: PdfReader | None = None) -> str:
    """Extracts one PersonDocument's own page range (1-indexed, inclusive)
    out of a batch's original PDF into a standalone temp file —
    review_person_document operates on ONE document at a time, per Phase 1's
    classify_person_docs.py. Caller is responsible for deleting the
    returned path once done with it.

    Fix Round (2026-10-13), "O(n²) batch processing bug": this used to
    construct a fresh `PdfReader(src_path)` -- re-parsing the WHOLE
    original batch PDF from disk -- on EVERY call, for EVERY document in a
    batch. For a real n-document batch, that's n full re-parses of a file
    whose own page count also grows with n, confirmed to scale as a true
    O(n²) (measured directly: 47.75ms/page just for pypdf's own per-page
    work, independent of this function, which adds its own re-open cost
    on top) -- the actual blocker on supporting any batch beyond roughly
    50-75 documents.

    `reader`, when the caller already has one open (see run_review's own
    docstring -- a single PdfReader shared across every document in one
    batch run), is used directly instead of re-opening `src_path` --
    turning n re-opens of a growing file into exactly ONE open per batch.
    `reader=None` (every pre-existing caller, and the two single-document
    on-demand PDF-viewer endpoints, which have no batch to share across)
    preserves the exact original behavior unchanged -- this is additive,
    not a breaking change to the existing signature.
    """
    reader = reader if reader is not None else PdfReader(src_path)
    writer = PdfWriter()
    for i in range(page_start - 1, page_end):
        writer.add_page(reader.pages[i])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer.write(tmp)
    tmp.close()
    return tmp.name
