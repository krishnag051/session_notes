import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import { Loader2 } from "lucide-react";

// Restored approach (from the earlier reviewer/frontend project's own
// PdfViewer.tsx — Krishna's real, previously-working implementation):
// fetch the PDF as a Blob and render it via the BROWSER'S OWN native PDF
// viewer (Chrome/Edge's PDFium, Firefox's pdf.js) in an <iframe>, using
// the PDF "open parameters" spec (#page=N, #view=FitH) those viewers
// already honor — not react-pdf/a custom canvas renderer. This renders
// noticeably better than a from-scratch canvas viewer, needs no bundled
// worker script (sidesteps the whole pdf.js-worker/nginx MIME-type class
// of bug hit two phases ago entirely), and gives a REAL page jump.
//
// BUG FIX (this phase): the LAST phase's default hash included
// `toolbar=0`, deliberately hiding the native viewer's own toolbar to
// reclaim vertical space. Krishna compared this directly against the
// reference project's own Treatment Plan page and wants that toolbar
// BACK — page indicator, zoom in/out, fit-width, rotate, download,
// print, fullscreen. Since this is already the browser's OWN native PDF
// viewer (not a custom-built one), simply NOT suppressing it is the
// entire fix — every one of those controls is already built into
// Chrome/Edge/Firefox's own PDF viewer for free, no new code needed.
//
// The remount-on-jump trick matters: re-setting an already-rendered
// iframe's `src` to the same blob: URL with a new "#page=N" hash does NOT
// reliably re-trigger the embedded viewer's open-parameters parse in
// Chrome/Edge's PDFium (confirmed in the reference project's own commit
// history) — only a genuine remount does. `key` below combines the hash
// with a monotonic jump counter so even re-clicking the SAME page number
// twice still forces a fresh remount (an identical hash alone would mean
// an identical key, and React would skip the remount as a no-op).
export interface PdfViewerHandle {
  goToPage: (page: number) => void;
}

export const PdfViewer = forwardRef<PdfViewerHandle, { src: string; className?: string }>(
  function PdfViewer({ src, className }, ref) {
    const [baseUrl, setBaseUrl] = useState<string | null>(null);
    const [hash, setHash] = useState("#view=FitH");
    const [jumpCount, setJumpCount] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    useImperativeHandle(ref, () => ({
      goToPage: (page: number) => {
        setHash(`#page=${page}`);
        setJumpCount((n) => n + 1);
      },
    }), []);

    useEffect(() => {
      let objectUrl: string | null = null;
      let cancelled = false;
      setLoading(true);
      setError(null);
      setBaseUrl(null);
      setHash("#view=FitH");
      setJumpCount(0);

      fetch(src)
        .then((res) => {
          if (!res.ok) throw new Error(`Failed to load PDF: ${res.status}`);
          return res.blob();
        })
        .then((blob) => {
          if (cancelled) return;
          objectUrl = URL.createObjectURL(blob);
          setBaseUrl(objectUrl);
        })
        .catch(() => {
          if (!cancelled) setError("Failed to load the PDF.");
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });

      return () => {
        cancelled = true;
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      };
    }, [src]);

    if (loading) {
      return (
        <div className={`flex items-center justify-center gap-2 bg-muted/40 text-sm text-muted-foreground ${className ?? ""}`}>
          <Loader2 className="h-5 w-5 animate-spin" /> Loading document…
        </div>
      );
    }
    if (error || !baseUrl) {
      return (
        <div className={`flex items-center justify-center bg-muted/40 p-6 text-center text-sm text-muted-foreground ${className ?? ""}`}>
          {error ?? "Couldn't load this document."}
        </div>
      );
    }

    return (
      <iframe
        key={`${hash}-${jumpCount}`}
        src={`${baseUrl}${hash}`}
        title="Document"
        className={`w-full border-0 ${className ?? ""}`}
      />
    );
  },
);
