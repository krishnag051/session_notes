import { createFileRoute, Link, useRouter } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, FileText, CheckCircle2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  getPersonDocument,
  getReviewOrNull,
  listPersonDocuments,
  listReviewers,
  markReviewed,
  markUnreviewed,
  type PersonDocumentSummary,
  type Reviewer,
  type RuleResultRow,
} from "@/lib/api";

export const Route = createFileRoute("/audits/$auditId")({
  loader: async ({ params }) => {
    const [personDocument, review] = await Promise.all([
      getPersonDocument(params.auditId),
      getReviewOrNull(params.auditId),
    ]);
    return { personDocument, review };
  },
  head: ({ loaderData }) => {
    const t = `${loaderData?.personDocument.client_name ?? "Unresolved"} — Audit detail`;
    return {
      meta: [
        { title: t },
        { name: "description", content: "Rule-by-rule compliance results for this session note." },
      ],
    };
  },
  component: AuditDetail,
});

const GROUPS = ["Failed", "Not Applicable", "Informational", "Passed"] as const;

function AuditFlagsBadge({ result }: { result: string }) {
  return (
    <Badge
      className={result === "pass" ? "bg-success/12 text-success hover:bg-success/12" : "bg-destructive/12 text-destructive hover:bg-destructive/12"}
      variant="secondary"
    >
      {result === "pass" ? "PASSED" : "FAILED"}
    </Badge>
  );
}

/** Simple typeahead, no login/auth — start typing, matching existing
 * Reviewer.name entries autocomplete; a name that doesn't exist yet just
 * gets typed as-is and auto-creates itself on submit (see
 * app/services/reviewers.py::get_or_create_reviewer). */
function ReviewerTypeahead({ value, onChange }: { value: string; onChange: (name: string) => void }) {
  const [suggestions, setSuggestions] = useState<Reviewer[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!value.trim()) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    listReviewers(value).then((reviewers) => {
      if (!cancelled) setSuggestions(reviewers);
    });
    return () => {
      cancelled = true;
    };
  }, [value]);

  return (
    <div className="relative w-64">
      <Input
        placeholder="Reviewed by…"
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && suggestions.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full rounded-md border bg-popover shadow-md">
          {suggestions.map((r) => (
            <li
              key={r.id}
              className="cursor-pointer px-3 py-2 text-sm hover:bg-accent"
              onMouseDown={() => {
                onChange(r.name);
                setOpen(false);
              }}
            >
              {r.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DocumentTabs({
  currentDocumentId,
  siblings,
}: {
  currentDocumentId: string;
  siblings: PersonDocumentSummary[] | null;
}) {
  if (!siblings || siblings.length <= 1) return null;

  return (
    <div className="mb-4 flex flex-wrap gap-2">
      {siblings.map((doc, i) => (
        <Link
          key={doc.id}
          to="/audits/$auditId"
          params={{ auditId: doc.id }}
          className={`rounded-md border px-3 py-1.5 text-sm ${
            doc.id === currentDocumentId ? "border-primary bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted"
          }`}
        >
          Document {i + 1} of {siblings.length}: {doc.service_code ?? "Unresolved"}
        </Link>
      ))}
    </div>
  );
}

function AuditDetail() {
  const router = useRouter();
  const { personDocument, review } = Route.useLoaderData();
  const [reviewerName, setReviewerName] = useState(review?.reviewed_by ?? "");
  const [siblings, setSiblings] = useState<PersonDocumentSummary[] | null>(null);

  useEffect(() => {
    setReviewerName(review?.reviewed_by ?? "");
  }, [review?.reviewed_by, review?.id]);

  useEffect(() => {
    if (!personDocument.person_id) return;
    listPersonDocuments(personDocument.person_id).then((docs) =>
      setSiblings(docs.filter((d) => d.batch_id === personDocument.batch_id)),
    );
  }, [personDocument.person_id, personDocument.batch_id]);

  const nextDocumentId = useMemo(() => {
    if (!siblings || siblings.length <= 1) return null;
    const idx = siblings.findIndex((d) => d.id === personDocument.id);
    if (idx === -1 || idx === siblings.length - 1) return null;
    return siblings[idx + 1]?.id ?? null;
  }, [siblings, personDocument.id]);

  async function handleMarkReviewedAndNext() {
    if (!review || !reviewerName.trim()) return;
    await markReviewed(review.id, reviewerName.trim());
    if (nextDocumentId) {
      router.navigate({ to: "/audits/$auditId", params: { auditId: nextDocumentId } });
    } else {
      router.invalidate();
    }
  }

  async function handleMarkUnreviewed() {
    if (!review) return;
    await markUnreviewed(review.id);
    router.invalidate();
  }

  if (!review) {
    return (
      <div>
        <Link to="/audits" className="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" /> Back to audits
        </Link>
        <DocumentTabs currentDocumentId={personDocument.id} siblings={siblings} />
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{personDocument.client_name ?? "Unresolved document"}</h1>
          {personDocument.service_code && <Badge variant="secondary">{personDocument.service_code}</Badge>}
          <span className="text-sm text-muted-foreground">DOS {personDocument.date_of_service}</span>
        </div>
        <Card className="shadow-card">
          <CardContent className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
            <FileText className="h-10 w-10" />
            <div className="text-base font-semibold text-foreground">This document hasn't been reviewed yet</div>
            <p className="max-w-sm text-sm">
              {personDocument.classification_confidence === "unresolved"
                ? "Its classification is unresolved — correct it before a review can run."
                : "Run the compliance review for this document to see findings here."}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div>
      <Link to="/audits" className="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to audits
      </Link>

      <DocumentTabs currentDocumentId={personDocument.id} siblings={siblings} />

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{review.client_name ?? "Unresolved document"}</h1>
        <AuditFlagsBadge result={review.audit_result} />
        {review.service_code && <Badge variant="secondary">{review.service_code}</Badge>}
        <span className="text-sm text-muted-foreground">
          DOS {review.date_of_service} · {review.provider_name ?? "—"} · Supervisor {review.bcba_name ?? "—"} · Score{" "}
          {review.score !== null ? `${review.score}%` : "—"}
        </span>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Card className="shadow-card">
          <CardContent className="p-0">
            <div className="border-b px-5 py-4 text-sm font-semibold">Document</div>
            <div className="flex h-[520px] flex-col items-center justify-center gap-3 bg-muted/40 text-muted-foreground">
              <FileText className="h-10 w-10" />
              <div className="text-sm">
                {(review.client_name ?? "document").replace(/\s+/g, "_").toLowerCase()}_{review.date_of_service}.pdf
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
                const items = review.grouped_results[group] ?? [];
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
                      {items.map((r) => (
                        <RuleRow key={r.id} rule={r} />
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-4 border-t px-5 py-4">
              {review.reviewed ? (
                <div className="flex items-center gap-2 text-sm text-success">
                  <CheckCircle2 className="h-4 w-4" />
                  Reviewed by {review.reviewed_by} · {review.reviewed_at?.slice(0, 10)}
                </div>
              ) : (
                <span className="text-sm text-muted-foreground">Not yet reviewed</span>
              )}
              <div className="flex items-center gap-2">
                {review.reviewed ? (
                  <Button variant="outline" onClick={handleMarkUnreviewed}>
                    Mark Unreviewed
                  </Button>
                ) : (
                  <>
                    <ReviewerTypeahead value={reviewerName} onChange={setReviewerName} />
                    <Button onClick={handleMarkReviewedAndNext} disabled={!reviewerName.trim()}>
                      Mark Reviewed{nextDocumentId ? " and Next" : ""}
                    </Button>
                  </>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function RuleRow({ rule }: { rule: RuleResultRow }) {
  const tone =
    rule.group === "Failed"
      ? "bg-destructive/12 text-destructive"
      : rule.group === "Passed"
        ? "bg-success/12 text-success"
        : "bg-muted text-muted-foreground";
  return (
    <li className="rounded-lg border p-3">
      <div className="flex items-start justify-between gap-3">
        <span className="text-sm font-medium">{rule.rule_id}</span>
        <span className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold ${tone}`}>
          {rule.final_status}
          {rule.is_overridden ? " (overridden)" : ""}
        </span>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{rule.final_finding}</p>
    </li>
  );
}
