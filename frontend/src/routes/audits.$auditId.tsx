import { createFileRoute, Link, notFound } from "@tanstack/react-router";
import { useState } from "react";
import { ArrowLeft, FileText, CheckCircle2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/routes/index";
import { AUDITS, CURRENT_USER, type RuleResult } from "@/lib/mock-data";

export const Route = createFileRoute("/audits/$auditId")({
  loader: ({ params }) => {
    const audit = AUDITS.find((a) => a.id === params.auditId);
    if (!audit) throw notFound();
    return { audit };
  },
  head: ({ loaderData }) => {
    if (!loaderData) {
      return { meta: [{ title: "Audit not found" }, { name: "robots", content: "noindex" }] };
    }
    const t = `${loaderData.audit.patient} — Audit detail`;
    return {
      meta: [
        { title: t },
        { name: "description", content: "Rule-by-rule compliance results for this session note." },
        { property: "og:title", content: t },
        { property: "og:description", content: "Rule-by-rule compliance results for this session note." },
      ],
    };
  },
  component: AuditDetail,
});

const GROUPS = ["Failed", "Not Applicable", "Informational", "Passed"] as const;

function AuditDetail() {
  const { audit } = Route.useLoaderData();
  const [reviewedBy, setReviewedBy] = useState<string | null>(null);

  return (
    <div>
      <Link to="/audits" className="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to audits
      </Link>

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{audit.patient}</h1>
        <StatusBadge status={audit.status} />
        <Badge variant="secondary">{audit.code}</Badge>
        <span className="text-sm text-muted-foreground">
          DOS {audit.dateOfService} · {audit.provider} · Supervisor {audit.bcba} · Score {audit.score}%
        </span>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Card className="shadow-card">
          <CardContent className="p-0">
            <div className="border-b px-5 py-4 text-sm font-semibold">Document</div>
            <div className="flex h-[520px] flex-col items-center justify-center gap-3 bg-muted/40 text-muted-foreground">
              <FileText className="h-10 w-10" />
              <div className="text-sm">
                {audit.patient.replace(/\s+/g, "_").toLowerCase()}_{audit.dateOfService}.pdf
              </div>
              <div className="text-xs">PDF preview placeholder</div>
            </div>
          </CardContent>
        </Card>

        <Card className="shadow-card">
          <CardContent className="p-0">
            <div className="border-b px-5 py-4 text-sm font-semibold">Rules checked</div>
            <div className="divide-y">
              {GROUPS.map((group) => {
                const items = audit.results.filter((r) => r.group === group);
                if (items.length === 0) return null;
                return (
                  <div key={group} className="px-5 py-4">
                    <div className="mb-3 flex items-center gap-2">
                      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        {group}
                      </span>
                      <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                        {items.length}
                      </span>
                    </div>
                    <ul className="space-y-3">
                      {items.map((r, i) => (
                        <RuleRow key={i} rule={r} />
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>

            <div className="flex items-center justify-between gap-4 border-t px-5 py-4">
              {reviewedBy ? (
                <div className="flex items-center gap-2 text-sm text-success">
                  <CheckCircle2 className="h-4 w-4" />
                  Reviewed by {reviewedBy} · {new Date().toLocaleDateString()}
                </div>
              ) : (
                <span className="text-sm text-muted-foreground">Not yet reviewed</span>
              )}
              <Button onClick={() => setReviewedBy(CURRENT_USER.name)} disabled={!!reviewedBy}>
                Mark Reviewed
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function RuleRow({ rule }: { rule: RuleResult }) {
  const tone =
    rule.group === "Failed"
      ? "bg-destructive/12 text-destructive"
      : rule.group === "Passed"
        ? "bg-success/12 text-success"
        : "bg-muted text-muted-foreground";
  return (
    <li className="rounded-lg border p-3">
      <div className="flex items-start justify-between gap-3">
        <span className="text-sm font-medium">{rule.question}</span>
        <span className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold ${tone}`}>{rule.answer}</span>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{rule.explanation}</p>
    </li>
  );
}
