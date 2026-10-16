import { useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { deleteBatchForever, listBatches, resumeBatchReviews, type BatchDetail } from "@/lib/api";

// Real-time-ish polling, not a websocket: GET /batches' own review_summary
// is already recomputed fresh on every request (backend/app/routers/
// batches.py::_review_summary queries the latest SessionNoteReview per
// document live) -- this just re-fetches it on an interval so a long-
// running batch's progress visibly advances instead of looking frozen for
// the whole page visit (2026-10-15 real complaint: "it just looks stuck").
// Only polls while at least one batch actually has work in flight --
// stops automatically once nothing is pending/processing, so a quiet page
// doesn't keep hitting the backend forever.
const POLL_INTERVAL_MS = 4000;

export const Route = createFileRoute("/upload-history")({
  loader: () => listBatches(),
  head: () => ({
    meta: [
      { title: "Upload History — Session Note Compliance" },
      { name: "description", content: "Every upload, independent of its audit results, newest first." },
      { property: "og:title", content: "Upload History — Session Note Compliance" },
      { property: "og:description", content: "Every upload, independent of its audit results, newest first." },
    ],
  }),
  component: UploadHistoryPage,
});

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function reviewedCount(summary: BatchDetail["review_summary"], totalDocuments: number): number {
  const inFlight = summary.pending + summary.processing;
  return Math.max(0, totalDocuments - inFlight);
}

function UploadHistoryPage() {
  const loaderBatches = Route.useLoaderData();
  const [batches, setBatches] = useState(loaderBatches);
  const [resumingId, setResumingId] = useState<string | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BatchDetail | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    setBatches(loaderBatches);
  }, [loaderBatches]);

  useEffect(() => {
    // "Actively in flight" for polling purposes means reviews_active, not
    // merely "has pending documents" — a PAUSED batch (reviews_active
    // false, pending > 0) isn't doing anything right now, so polling it on
    // a 4s interval would just be wasted requests until someone clicks Resume.
    const anyInFlight = batches.some((b) => !b.auto_review_skipped && b.reviews_active);
    if (!anyInFlight) return;

    const id = window.setInterval(() => {
      listBatches()
        .then(setBatches)
        .catch(() => {
          // A transient poll failure shouldn't surface as an error state on
          // a page that's just passively refreshing — the next tick retries.
        });
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [batches]);

  function handleResume(batchId: string) {
    setResumeError(null);
    setResumingId(batchId);
    resumeBatchReviews(batchId)
      .then((updated) => {
        setBatches((prev) => prev.map((b) => (b.id === updated.id ? updated : b)));
      })
      .catch((err: Error) => {
        setResumeError(err.message);
      })
      .finally(() => setResumingId(null));
  }

  function handleDeleteForever() {
    if (!deleteTarget) return;
    setDeleteError(null);
    setDeleting(true);
    deleteBatchForever(deleteTarget.id, "k.kumar@masterfaster.org", deleteReason.trim() || "no reason given")
      .then(() => {
        setBatches((prev) => prev.filter((b) => b.id !== deleteTarget.id));
        setDeleteTarget(null);
        setDeleteReason("");
      })
      .catch((err: Error) => {
        setDeleteError(err.message);
      })
      .finally(() => setDeleting(false));
  }

  return (
    <div>
      <PageHeader
        title="Upload History"
        subtitle="Every upload that successfully processed, independent of its audit results — most recent first."
      />

      {resumeError && (
        <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          Couldn't resume: {resumeError}
        </div>
      )}
      {deleteError && (
        <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          Couldn't delete: {deleteError}
        </div>
      )}

      <Card className="shadow-card">
        <CardContent className="p-0">
          {batches.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">No uploads yet.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="px-5 py-3 font-medium">File</th>
                    <th className="px-5 py-3 font-medium">Uploaded</th>
                    <th className="px-5 py-3 font-medium">Uploaded By</th>
                    <th className="px-5 py-3 font-medium">Documents</th>
                    <th className="px-5 py-3 font-medium">Status</th>
                    <th className="px-5 py-3 font-medium">Real Cost</th>
                    <th className="px-5 py-3 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {batches.map((batch) => {
                    const summary = batch.review_summary;
                    const done = reviewedCount(summary, batch.documents.length);
                    const isPaused = !batch.auto_review_skipped && !batch.reviews_active && summary.pending > 0;
                    const isActivelyReviewing = !batch.auto_review_skipped && batch.reviews_active;
                    return (
                      <tr key={batch.id} className="border-b last:border-0 hover:bg-muted/50">
                        <td className="max-w-xs truncate px-5 py-3 font-medium" title={batch.original_filename ?? undefined}>
                          {batch.original_filename ?? "(filename unavailable)"}
                        </td>
                        <td className="whitespace-nowrap px-5 py-3 text-muted-foreground">
                          {formatDateTime(batch.uploaded_at)}
                        </td>
                        <td className="px-5 py-3 text-muted-foreground">{batch.uploaded_by ?? "—"}</td>
                        <td className="px-5 py-3 text-muted-foreground">{batch.documents.length}</td>
                        <td className="px-5 py-3">
                          {batch.auto_review_skipped ? (
                            <Badge variant="secondary" className="bg-muted text-muted-foreground">
                              Not auto-reviewed
                            </Badge>
                          ) : isPaused ? (
                            <Badge variant="secondary" className="bg-destructive/10 text-destructive">
                              Paused — {done} of {batch.documents.length} done, {summary.pending} remaining
                            </Badge>
                          ) : isActivelyReviewing ? (
                            <Badge variant="secondary" className="bg-warning/20 text-warning-foreground">
                              Reviewing ({done} of {batch.documents.length} done)
                            </Badge>
                          ) : (
                            <Badge variant="secondary" className="bg-success/12 text-success">
                              Complete
                            </Badge>
                          )}
                        </td>
                        <td className="px-5 py-3 whitespace-nowrap text-muted-foreground">
                          {/* The same real, already-tracked total_spend_usd used for the
                              batch spend cap itself — never a separate estimate. */}
                          ${summary.total_spend_usd.toFixed(2)}
                        </td>
                        <td className="px-5 py-3 text-right">
                          <div className="flex justify-end gap-2">
                            {isPaused && (
                              <Button
                                variant="default"
                                size="sm"
                                disabled={resumingId === batch.id}
                                onClick={() => handleResume(batch.id)}
                              >
                                {resumingId === batch.id ? "Resuming…" : "Resume"}
                              </Button>
                            )}
                            <Button asChild variant="outline" size="sm">
                              <Link to="/audits" search={{ batch_id: batch.id }}>
                                View Results
                              </Link>
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label="Delete batch forever"
                              disabled={batch.reviews_active}
                              title={
                                batch.reviews_active
                                  ? "Can't delete while a review run is active for this batch"
                                  : "Delete this batch forever"
                              }
                              onClick={() => {
                                setDeleteError(null);
                                setDeleteReason("");
                                setDeleteTarget(batch);
                              }}
                            >
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this batch forever?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes all {deleteTarget?.documents.length ?? 0} documents in{" "}
              <strong>{deleteTarget?.original_filename ?? "this batch"}</strong>, every one of their reviews, and all
              of their findings. This <strong>cannot be undone</strong> — there is no way to recover it afterward.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="batch-delete-reason">Reason (optional, kept in the audit trail)</Label>
            <Input
              id="batch-delete-reason"
              value={deleteReason}
              onChange={(e) => setDeleteReason(e.target.value)}
              placeholder="e.g. misconfigured spend cap, bad partial run"
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleting}
              onClick={(e) => {
                e.preventDefault();
                handleDeleteForever();
              }}
            >
              {deleting ? "Deleting…" : `Delete all ${deleteTarget?.documents.length ?? 0} documents`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
