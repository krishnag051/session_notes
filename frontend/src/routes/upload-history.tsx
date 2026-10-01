import { createFileRoute, Link } from "@tanstack/react-router";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { listBatches } from "@/lib/api";

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

function UploadHistoryPage() {
  const batches = Route.useLoaderData();

  return (
    <div>
      <PageHeader
        title="Upload History"
        subtitle="Every upload that successfully processed, independent of its audit results — most recent first."
      />

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
                    <th className="px-5 py-3 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {batches.map((batch) => {
                    const summary = batch.review_summary;
                    const inFlight = summary.pending + summary.processing;
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
                          ) : inFlight > 0 ? (
                            <Badge variant="secondary" className="bg-warning/20 text-warning-foreground">
                              Reviewing ({inFlight} left)
                            </Badge>
                          ) : (
                            <Badge variant="secondary" className="bg-success/12 text-success">
                              Complete
                            </Badge>
                          )}
                        </td>
                        <td className="px-5 py-3 text-right">
                          <Button asChild variant="outline" size="sm">
                            <Link to="/audits" search={{ batch_id: batch.id }}>
                              View Results
                            </Link>
                          </Button>
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
    </div>
  );
}
