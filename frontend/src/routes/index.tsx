import { createFileRoute, Link } from "@tanstack/react-router";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { getAuditSummary, listAudits } from "@/lib/api";

export const Route = createFileRoute("/")({
  loader: async () => {
    const [summary, audits] = await Promise.all([getAuditSummary(), listAudits({ pageSize: 10 })]);
    return { summary, recent: audits.items };
  },
  head: () => ({
    meta: [
      { title: "Dashboard — Session Note Compliance" },
      { name: "description", content: "Audit volume, coverage and pass rate for ABA session notes." },
      { property: "og:title", content: "Dashboard — Session Note Compliance" },
      { property: "og:description", content: "Audit volume, coverage and pass rate for ABA session notes." },
    ],
  }),
  component: Dashboard,
});

function Dashboard() {
  const { summary, recent } = Route.useLoaderData();

  const stats = [
    { label: "Total Audits Completed", value: summary.total_audits_completed.toLocaleString() },
    { label: "Total Patients Covered", value: summary.total_patients_covered.toLocaleString() },
    { label: "Audits This Week", value: summary.audits_this_week.toLocaleString() },
    { label: "Pass Rate", value: summary.pass_rate !== null ? `${summary.pass_rate}%` : "—" },
  ];

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Compliance overview across all audited session notes." />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((s) => (
          <Card key={s.label} className="shadow-card">
            <CardContent className="p-5">
              <div className="text-sm text-muted-foreground">{s.label}</div>
              <div className="mt-2 text-3xl font-semibold tracking-tight">{s.value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="mt-6 shadow-card">
        <CardContent className="p-0">
          <div className="border-b px-5 py-4 text-sm font-semibold">Recent audits</div>
          {recent.length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-muted-foreground">
              No audits yet — upload a session note to get started.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-5 py-3 font-medium">Patient</th>
                  <th className="px-5 py-3 font-medium">Code</th>
                  <th className="px-5 py-3 font-medium">Date of service</th>
                  <th className="px-5 py-3 font-medium">Result</th>
                  <th className="px-5 py-3 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {recent.map((a) => (
                  <tr key={a.person_document_id} className="border-b last:border-0 hover:bg-muted/50">
                    <td className="px-5 py-3 font-medium">{a.client ?? "Unresolved"}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.code ?? "—"}</td>
                    <td className="px-5 py-3 text-muted-foreground">{a.date_of_service ?? "—"}</td>
                    <td className="px-5 py-3">
                      {a.audit_flags ? <StatusBadge status={a.audit_flags} /> : (
                        <span className="text-xs text-muted-foreground">Not yet reviewed</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Link
                        to="/audits/$auditId"
                        params={{ auditId: a.person_document_id }}
                        className="text-sm font-medium text-primary hover:underline"
                      >
                        View
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export function StatusBadge({ status }: { status: "PASSED" | "FAILED" }) {
  return (
    <Badge
      className={
        status === "PASSED"
          ? "bg-success/12 text-success hover:bg-success/12"
          : "bg-destructive/12 text-destructive hover:bg-destructive/12"
      }
      variant="secondary"
    >
      {status}
    </Badge>
  );
}
