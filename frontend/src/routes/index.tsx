import { createFileRoute, Link } from "@tanstack/react-router";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AUDITS, DASHBOARD_STATS } from "@/lib/mock-data";

export const Route = createFileRoute("/")({
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
  const recent = AUDITS.slice(0, 10);

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Compliance overview across all audited session notes." />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {DASHBOARD_STATS.map((s) => (
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
                <tr key={a.id} className="border-b last:border-0 hover:bg-muted/50">
                  <td className="px-5 py-3 font-medium">{a.patient}</td>
                  <td className="px-5 py-3 text-muted-foreground">{a.code}</td>
                  <td className="px-5 py-3 text-muted-foreground">{a.dateOfService}</td>
                  <td className="px-5 py-3">
                    <StatusBadge status={a.status} />
                  </td>
                  <td className="px-5 py-3 text-right">
                    <Link
                      to="/audits/$auditId"
                      params={{ auditId: a.id }}
                      className="text-sm font-medium text-primary hover:underline"
                    >
                      View
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}

export function StatusBadge({ status }: { status: "Passed" | "Failed" }) {
  return (
    <Badge
      className={
        status === "Passed"
          ? "bg-success/12 text-success hover:bg-success/12"
          : "bg-destructive/12 text-destructive hover:bg-destructive/12"
      }
      variant="secondary"
    >
      {status}
    </Badge>
  );
}
