import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { listAudits, type AuditRow } from "@/lib/api";

function AuditFlagsBadge({ flags }: { flags: AuditRow["audit_flags"] }) {
  if (flags === null) {
    return <Badge variant="secondary" className="bg-muted text-muted-foreground">—</Badge>;
  }
  return (
    <Badge
      className={flags === "PASSED" ? "bg-success/12 text-success hover:bg-success/12" : "bg-destructive/12 text-destructive hover:bg-destructive/12"}
      variant="secondary"
    >
      {flags}
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
  loader: () => listAudits(),
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
  const audits = Route.useLoaderData();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("All");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const rows = useMemo(
    () =>
      audits.filter((a) => {
        if (search && !(a.client ?? "").toLowerCase().includes(search.toLowerCase())) return false;
        if (status !== "All" && a.audit_flags !== status) return false;
        if (from && (a.date_of_service ?? "") < from) return false;
        if (to && (a.date_of_service ?? "") > to) return false;
        return true;
      }),
    [audits, search, status, from, to],
  );

  return (
    <div>
      <PageHeader title="Audits" subtitle="All processed patient documents, newest date of service first." />

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
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.person_document_id} className="border-b last:border-0 hover:bg-muted/50">
                    <td className="px-5 py-3 font-medium">
                      <Link
                        to="/audits/$auditId"
                        params={{ auditId: a.person_document_id }}
                        className="hover:text-primary hover:underline"
                      >
                        {a.client ?? "Unresolved"}
                      </Link>
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
                      <AuditFlagsBadge flags={a.audit_flags} />
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Button asChild size="sm" variant="outline">
                        <Link to="/audits/$auditId" params={{ auditId: a.person_document_id }}>
                          View
                        </Link>
                      </Button>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={12} className="px-5 py-12 text-center text-muted-foreground">
                      No audits match these filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
