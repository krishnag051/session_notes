import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Loader2, ChevronLeft, ChevronRight } from "lucide-react";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AuditActionsMenu } from "@/components/AuditActionsMenu";
import { listAudits, type AuditListResult, type AuditRow } from "@/lib/api";

const PAGE_SIZE = 15;

function AuditFlagsBadge({ row }: { row: AuditRow }) {
  if (row.processing_status === "pending" || row.processing_status === "processing") {
    return (
      <Badge variant="secondary" className="flex w-fit items-center gap-1 bg-muted text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" /> {row.processing_status === "pending" ? "Queued" : "Reviewing…"}
      </Badge>
    );
  }
  if (row.processing_status === "failed") {
    return <Badge variant="secondary" className="bg-destructive/12 text-destructive">Review failed</Badge>;
  }
  if (row.processing_status === "skipped_spend_cap") {
    return <Badge variant="secondary" className="bg-warning/20 text-warning-foreground">Skipped (spend cap)</Badge>;
  }
  if (row.processing_status === "cancelled_spend_cap") {
    return <Badge variant="secondary" className="bg-warning/20 text-warning-foreground">Cancelled (spend cap)</Badge>;
  }
  if (row.processing_status === "no_applicable_rules") {
    return <Badge variant="secondary" className="bg-muted text-muted-foreground">No rules apply</Badge>;
  }
  if (row.audit_flags === null) {
    return <Badge variant="secondary" className="bg-muted text-muted-foreground">—</Badge>;
  }
  return (
    <Badge
      className={row.audit_flags === "PASSED" ? "bg-success/12 text-success hover:bg-success/12" : "bg-destructive/12 text-destructive hover:bg-destructive/12"}
      variant="secondary"
    >
      {row.audit_flags}
    </Badge>
  );
}

function ReviewStatusBadge({ status }: { status: AuditRow["review_status"] }) {
  const reviewed = status === "Reviewed";
  return (
    <Badge
      className={reviewed ? "bg-muted text-foreground hover:bg-muted" : "bg-warning/20 text-warning-foreground hover:bg-warning/20"}
      variant="secondary"
    >
      {status}
    </Badge>
  );
}

export const Route = createFileRoute("/audits/")({
  // batch_id: the Upload History tab's own "View Results" link — scopes
  // this list down to just one upload, reusing this page's existing
  // filter/fetch machinery rather than a separate results view.
  validateSearch: (search: Record<string, unknown>) => {
    const batchId = search["batch_id"];
    return typeof batchId === "string" ? { batch_id: batchId } : {};
  },
  loaderDeps: ({ search }) => ({ batchId: search.batch_id }),
  loader: ({ deps }) => listAudits({ pageSize: PAGE_SIZE, batchId: deps.batchId }),
  head: () => ({
    meta: [
      { title: "Audits — Session Note Compliance" },
      { name: "description", content: "Every audited ABA session note with pass/fail status and score." },
      { property: "og:title", content: "Audits — Session Note Compliance" },
      { property: "og:description", content: "Every audited ABA session note with pass/fail status and score." },
    ],
  }),
  component: AuditsList,
});

function AuditsList() {
  const loaderResult = Route.useLoaderData();
  const { batch_id: batchId } = Route.useSearch();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [status, setStatus] = useState("All");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<AuditListResult>(loaderResult);
  const [loading, setLoading] = useState(false);

  // Debounce the free-text search so every keystroke doesn't fire its own
  // request against the server-side filter.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Any filter change resets back to page 1 — a filter narrowing the
  // result set to fewer pages than the current one would otherwise land
  // on an out-of-range page.
  useEffect(() => {
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch, status, from, to, showArchived]);

  async function refetch() {
    setLoading(true);
    try {
      setResult(
        await listAudits({
          includeArchived: showArchived,
          search: debouncedSearch || undefined,
          status: status === "All" ? undefined : (status as "PASSED" | "FAILED"),
          dateFrom: from || undefined,
          dateTo: to || undefined,
          batchId,
          page,
          pageSize: PAGE_SIZE,
        }),
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch, status, from, to, showArchived, batchId, page]);

  // Some rows may still be pending/processing (a batch's reviews run one
  // at a time in the background) — poll so results appear here as they
  // complete, instead of only on a manual page refresh. Only bothers
  // polling while at least one row on THIS page is actually in flight.
  const hasInFlightRows = result.items.some((a) => a.processing_status === "pending" || a.processing_status === "processing");
  useEffect(() => {
    if (!hasInFlightRows) return;
    const timer = setInterval(refetch, 4000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasInFlightRows]);

  const totalPages = Math.max(1, Math.ceil(result.total / PAGE_SIZE));

  return (
    <div>
      <PageHeader title="Audits" subtitle="All processed patient documents, most recently uploaded first." />

      {batchId && (
        <div className="mb-4 flex items-center justify-between rounded-lg border bg-muted/40 px-4 py-2 text-sm">
          <span className="text-muted-foreground">Showing only documents from one upload.</span>
          <Link to="/audits" className="font-medium text-primary hover:underline">
            Clear filter
          </Link>
        </div>
      )}

      <Card className="shadow-card">
        <CardContent className="p-0">
          <div className="flex flex-wrap items-end gap-3 border-b p-4">
            <div className="min-w-56 flex-1">
              <Input
                placeholder="Search by patient name…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-2">
              <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-40" />
              <span className="text-sm text-muted-foreground">to</span>
              <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-40" />
            </div>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="All">All statuses</SelectItem>
                <SelectItem value="PASSED">Passed</SelectItem>
                <SelectItem value="FAILED">Failed</SelectItem>
              </SelectContent>
            </Select>
            <div className="flex items-center gap-2 pb-0.5">
              <Switch id="show-archived" checked={showArchived} onCheckedChange={setShowArchived} />
              <Label htmlFor="show-archived" className="text-sm font-normal text-muted-foreground">
                Show archived
              </Label>
            </div>
            {(search || status !== "All" || from || to) && (
              <Button
                variant="ghost"
                onClick={() => {
                  setSearch("");
                  setStatus("All");
                  setFrom("");
                  setTo("");
                }}
              >
                Clear
              </Button>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-5 py-3 font-medium">Client</th>
                  <th className="px-5 py-3 font-medium">Code</th>
                  <th className="px-5 py-3 font-medium">Date of service</th>
                  <th className="px-5 py-3 font-medium">Provider</th>
                  <th className="px-5 py-3 font-medium">BCBA</th>
                  <th className="px-5 py-3 font-medium">Start time</th>
                  <th className="px-5 py-3 font-medium">End time</th>
                  <th className="px-5 py-3 font-medium">Score</th>
                  <th className="px-5 py-3 font-medium">Reviewed by</th>
                  <th className="px-5 py-3 font-medium">Review status</th>
                  <th className="px-5 py-3 font-medium">Audit flags</th>
                  <th className="px-5 py-3 font-medium"></th>
                  <th className="px-5 py-3 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {result.items.map((a) => (
                  <tr
                    key={a.person_document_id}
                    className={`border-b last:border-0 hover:bg-muted/50 ${!a.active ? "opacity-50" : ""}`}
                  >
                    <td className="px-5 py-3 font-medium">
                      <Link
                        to="/audits/$auditId"
                        params={{ auditId: a.person_document_id }}
                        className="hover:text-primary hover:underline"
                      >
                        {a.client ?? "Unresolved"}
                      </Link>
                      {!a.active && <Badge variant="secondary" className="ml-2 bg-muted text-muted-foreground">Archived</Badge>}
                    </td>
                    <td className="px-5 py-3 text-muted-foreground">{a.code}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.date_of_service}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.provider ?? "—"}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.bcba ?? "—"}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.start_time ?? "—"}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.end_time ?? "—"}</td>
                    <td className="px-5 py-3 font-medium">{a.score !== null ? `${a.score}%` : "—"}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.reviewed_by ?? "—"}</td>
                    <td className="px-5 py-3">
                      <ReviewStatusBadge status={a.review_status} />
                    </td>
                    <td className="px-5 py-3">
                      <AuditFlagsBadge row={a} />
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Button asChild size="sm" variant="outline">
                        <Link to="/audits/$auditId" params={{ auditId: a.person_document_id }}>
                          View
                        </Link>
                      </Button>
                    </td>
                    <td className="px-2 py-3 text-right">
                      <AuditActionsMenu
                        personDocumentId={a.person_document_id}
                        active={a.active}
                        onChanged={refetch}
                        onDeleted={refetch}
                      />
                    </td>
                  </tr>
                ))}
                {result.items.length === 0 && (
                  <tr>
                    <td colSpan={13} className="px-5 py-12 text-center text-muted-foreground">
                      {loading ? "Loading…" : "No audits match these filters."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between border-t px-5 py-3 text-sm text-muted-foreground">
            <span>{result.total.toLocaleString()} total</span>
            <div className="flex items-center gap-3">
              <Button
                variant="outline"
                size="icon"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                aria-label="Previous page"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span>
                Page {page} of {totalPages}
              </span>
              <Button
                variant="outline"
                size="icon"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                aria-label="Next page"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
