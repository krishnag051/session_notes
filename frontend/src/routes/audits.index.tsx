import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { PageHeader } from "@/components/AppSidebar";
import { StatusBadge } from "@/routes/index";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AUDITS } from "@/lib/mock-data";

export const Route = createFileRoute("/audits/")({
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
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("All");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const rows = useMemo(
    () =>
      AUDITS.filter((a) => {
        if (search && !a.patient.toLowerCase().includes(search.toLowerCase())) return false;
        if (status !== "All" && a.status !== status) return false;
        if (from && a.dateOfService < from) return false;
        if (to && a.dateOfService > to) return false;
        return true;
      }),
    [search, status, from, to],
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
                <SelectItem value="Passed">Passed</SelectItem>
                <SelectItem value="Failed">Failed</SelectItem>
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
                  <th className="px-5 py-3 font-medium">Patient</th>
                  <th className="px-5 py-3 font-medium">Code</th>
                  <th className="px-5 py-3 font-medium">Date of service</th>
                  <th className="px-5 py-3 font-medium">Provider</th>
                  <th className="px-5 py-3 font-medium">BCBA</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Score</th>
                  <th className="px-5 py-3 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id} className="border-b last:border-0 hover:bg-muted/50">
                    <td className="px-5 py-3 font-medium">
                      <Link
                        to="/audits/$auditId"
                        params={{ auditId: a.id }}
                        className="hover:text-primary hover:underline"
                      >
                        {a.patient}
                      </Link>
                    </td>
                    <td className="px-5 py-3 text-muted-foreground">{a.code}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.dateOfService}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.provider}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.bcba}</td>
                    <td className="px-5 py-3">
                      <StatusBadge status={a.status} />
                    </td>
                    <td className="px-5 py-3 font-medium">{a.score}%</td>
                    <td className="px-5 py-3 text-right">
                      <Button asChild size="sm" variant="outline">
                        <Link to="/audits/$auditId" params={{ auditId: a.id }}>
                          View
                        </Link>
                      </Button>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-5 py-12 text-center text-muted-foreground">
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
