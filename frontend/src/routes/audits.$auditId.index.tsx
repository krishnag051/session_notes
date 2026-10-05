import { createFileRoute, Link, useRouter } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  FileText,
  CheckCircle2,
  Loader2,
  AlertTriangle,
  FileSearch,
  Clock,
  Download,
  ChevronsDownUp,
  ChevronsUpDown,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { PdfViewer, type PdfViewerHandle } from "@/components/PdfViewer";
import { AuditActionsMenu } from "@/components/AuditActionsMenu";
import { findingReason, normalizePages } from "@/lib/findings";
import { downloadRuleResultsCsv } from "@/lib/csv-export";
import {
  getExtractionOrNull,
  getPersonDocumentOrNull,
  getReviewOrNull,
  listPersonDocuments,
  listReviewers,
  markReviewed,
  markUnreviewed,
  personDocumentActivityStatementPdfUrl,
  personDocumentPdfUrl,
  type PersonDocumentSummary,
  type Reviewer,
  type RuleResultRow,
} from "@/lib/api";

export const Route = createFileRoute("/audits/$auditId/")({
  loader: async ({ params }) => {
    const [personDocument, review, extraction] = await Promise.all([
      getPersonDocumentOrNull(params.auditId),
      getReviewOrNull(params.auditId),
      getExtractionOrNull(params.auditId),
    ]);
    return { personDocument, review, extraction };
  },
  head: ({ loaderData }) => {
    const t = `${loaderData?.personDocument?.client_name ?? "Unresolved"} — Audit detail`;
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
type Group = (typeof GROUPS)[number];

// Subtle tint per group — calm, not loud full-saturation fill, matching
// Brellium's own restrained styling (Krishna's own feedback).
const GROUP_STYLE: Record<Group, { border: string; bg: string; chip: string }> = {
  Failed: { border: "border-l-destructive", bg: "bg-destructive/5", chip: "bg-destructive/12 text-destructive" },
  "Not Applicable": { border: "border-l-muted-foreground/40", bg: "bg-muted/30", chip: "bg-muted text-muted-foreground" },
  Informational: { border: "border-l-blue-400", bg: "bg-blue-400/5", chip: "bg-blue-400/15 text-blue-700 dark:text-blue-300" },
  Passed: { border: "border-l-success", bg: "bg-success/5", chip: "bg-success/12 text-success" },
};

// Both side panels fill whatever vertical space the viewport actually has
// left below the (now much slimmer) header — not a fixed pixel guess.
// Approximates everything above them: top bar + tabs + title row + chip
// row + gaps + the page's own top/bottom padding.
const PANEL_HEIGHT = "h-[calc(100vh-15rem)] min-h-[520px]";

function AuditFlagsBadge({ result }: { result: "pass" | "fail" | null }) {
  if (result === null) return null;
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
    <div className="mb-3 flex flex-wrap gap-2">
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

/** The "at a glance" identity/status bar Krishna's Brellium screenshots
 * show — Client/Provider/BCBA/Code/Date of Service/Start/End/Score/
 * Reviewed By, each a small bordered chip (label above, value below). */
function HeaderChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-sm font-medium">{value}</div>
    </div>
  );
}

function ProcessingStatusCard({ status, errorMessage }: { status: string; errorMessage: string | null }) {
  if (status === "failed") {
    return (
      <Card className="shadow-card">
        <CardContent className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
          <AlertTriangle className="h-10 w-10 text-destructive" />
          <div className="text-base font-semibold text-foreground">This document's review failed</div>
          <p className="max-w-md text-sm">{errorMessage ?? "No error detail was recorded."}</p>
        </CardContent>
      </Card>
    );
  }
  if (status === "skipped_spend_cap") {
    return (
      <Card className="shadow-card">
        <CardContent className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
          <AlertTriangle className="h-10 w-10 text-warning-foreground" />
          <div className="text-base font-semibold text-foreground">Skipped — batch spend cap reached</div>
          <p className="max-w-md text-sm">
            This document's batch had already spent up to its cap by the time this document's turn came up, so its
            review was never started. Re-run it manually once ready.
          </p>
        </CardContent>
      </Card>
    );
  }
  if (status === "cancelled_spend_cap") {
    return (
      <Card className="shadow-card">
        <CardContent className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
          <AlertTriangle className="h-10 w-10 text-warning-foreground" />
          <div className="text-base font-semibold text-foreground">Cancelled — this document's own spend cap was reached</div>
          <p className="max-w-md text-sm">
            {errorMessage ?? "This document's review spent up to its own per-document limit before finishing and was stopped."}
            {" "}Re-run it manually if needed.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card className="shadow-card">
      <CardContent className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
        <Loader2 className="h-10 w-10 animate-spin text-primary" />
        <div className="text-base font-semibold text-foreground">
          {status === "processing" ? "Review in progress…" : "Queued for review…"}
        </div>
        <p className="max-w-sm text-sm">This runs automatically in the background — this page updates on its own.</p>
      </CardContent>
    </Card>
  );
}

function FindingsPanel({
  groupedResults,
  onGoToPage,
  emptyMessage,
}: {
  groupedResults: Record<Group, RuleResultRow[]>;
  onGoToPage: (page: number) => void;
  emptyMessage?: string | undefined;
}) {
  const groupsWithItems = GROUPS.filter((g) => (groupedResults[g] ?? []).length > 0);
  // Failed starts expanded (what a reviewer needs to see first); the rest
  // start collapsed.
  const [openGroups, setOpenGroups] = useState<string[]>(["Failed"]);
  const allExpanded = groupsWithItems.length > 0 && groupsWithItems.every((g) => openGroups.includes(g));

  return (
    <Card className={`shadow-card ${PANEL_HEIGHT} flex flex-col`}>
      <CardContent className="flex min-h-0 flex-1 flex-col p-0">
        <div className="flex items-center justify-between border-b px-5 py-4">
          <span className="text-sm font-semibold">Rules checked</span>
          {groupsWithItems.length > 0 && (
            <Button
              variant="ghost"
              size="icon"
              aria-label={allExpanded ? "Collapse all" : "Expand all"}
              title={allExpanded ? "Collapse all" : "Expand all"}
              onClick={() => setOpenGroups(allExpanded ? [] : [...groupsWithItems])}
            >
              {allExpanded ? <ChevronsDownUp className="h-4 w-4" /> : <ChevronsUpDown className="h-4 w-4" />}
            </Button>
          )}
        </div>

        {groupsWithItems.length === 0 ? (
          // BUG FIX: this used to render nothing at all — a blank panel
          // with no explanation, indistinguishable from a real loading/
          // fetch failure. A document whose service_code genuinely has no
          // rules.json entries (97156 — see has_applicable_rules) reaches
          // this deliberately, with a clear reason; anything else landing
          // here (shouldn't happen once status="complete") still gets an
          // honest message instead of silence.
          <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center text-muted-foreground">
            <FileText className="h-8 w-8" />
            <p className="max-w-sm text-sm">
              {emptyMessage ?? "No rule findings were recorded for this document."}
            </p>
          </div>
        ) : (
        <Accordion type="multiple" value={openGroups} onValueChange={setOpenGroups} className="min-h-0 flex-1 overflow-y-auto">
          {groupsWithItems.map((group) => {
            const items = groupedResults[group] ?? [];
            const style = GROUP_STYLE[group];
            return (
              <AccordionItem key={group} value={group} className={`border-l-4 px-5 ${style.border} ${style.bg}`}>
                <AccordionTrigger className="hover:no-underline">
                  <span className="flex items-center gap-2">
                    <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{group}</span>
                    <span className="rounded-full bg-background px-2 py-0.5 text-xs text-muted-foreground">
                      {items.length}
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  <ul className="space-y-2">
                    {items.map((r) => (
                      <RuleRow key={r.id} rule={r} chipClass={style.chip} onGoToPage={onGoToPage} />
                    ))}
                  </ul>
                </AccordionContent>
              </AccordionItem>
            );
          })}
        </Accordion>
        )}
      </CardContent>
    </Card>
  );
}

function AuditDetail() {
  const router = useRouter();
  const { personDocument, review, extraction } = Route.useLoaderData();
  const [reviewerName, setReviewerName] = useState(review?.reviewed_by ?? "");
  const [siblings, setSiblings] = useState<PersonDocumentSummary[] | null>(null);
  // ALL of this person's active documents (every batch, not just this
  // one) — used to find a genuinely previous session note of the same
  // service_code for the action row's "View Previous Session Note" link.
  const [allPersonDocs, setAllPersonDocs] = useState<PersonDocumentSummary[] | null>(null);
  const pdfRef = useRef<PdfViewerHandle>(null);

  useEffect(() => {
    setReviewerName(review?.reviewed_by ?? "");
  }, [review?.reviewed_by, review?.id]);

  useEffect(() => {
    if (!personDocument?.person_id) return;
    listPersonDocuments(personDocument.person_id).then((docs) => {
      setSiblings(docs.filter((d) => d.batch_id === personDocument.batch_id));
      setAllPersonDocs(docs);
    });
  }, [personDocument?.person_id, personDocument?.batch_id]);

  const previousSessionNote = useMemo(() => {
    if (!allPersonDocs || !personDocument) return null;
    const sameCode = allPersonDocs
      .filter((d) => d.id !== personDocument.id && d.service_code === personDocument.service_code && d.date_of_service)
      .sort((a, b) => (b.date_of_service ?? "").localeCompare(a.date_of_service ?? ""));
    return sameCode[0] ?? null;
  }, [allPersonDocs, personDocument]);

  // The review runs in the background one document at a time — poll while
  // it's still pending/processing so this page updates itself the moment
  // it finishes, instead of requiring a manual refresh.
  const inFlight = review?.status === "pending" || review?.status === "processing";
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(() => router.invalidate(), 3000);
    return () => clearInterval(timer);
  }, [inFlight, router]);

  const nextDocumentId = useMemo(() => {
    if (!siblings || siblings.length <= 1 || !personDocument) return null;
    const idx = siblings.findIndex((d) => d.id === personDocument.id);
    if (idx === -1 || idx === siblings.length - 1) return null;
    return siblings[idx + 1]?.id ?? null;
  }, [siblings, personDocument]);

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

  function handleDownloadCsv() {
    if (!review) return;
    const allResults = Object.values(review.grouped_results).flat();
    const safeName = (review.client_name ?? "unresolved")
      .replace(/[^a-z0-9]+/gi, "_")
      .toLowerCase();
    downloadRuleResultsCsv(
      allResults,
      `${safeName}_${review.service_code ?? "document"}_rule_results.csv`,
    );
  }

  if (!personDocument) {
    return (
      <div>
        <Link to="/audits" className="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" /> Back to audits
        </Link>
        <Card className="shadow-card">
          <CardContent className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
            <AlertTriangle className="h-10 w-10" />
            <div className="text-base font-semibold text-foreground">This audit no longer exists</div>
            <p className="max-w-sm text-sm">
              It may have been permanently deleted. If you followed a link or bookmark to get here, it's now stale.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const topBar = (
    <div className="mb-3 flex items-center justify-between">
      <Link to="/audits" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to audits
      </Link>
      <AuditActionsMenu
        personDocumentId={personDocument.id}
        active={personDocument.active}
        onChanged={() => router.invalidate()}
        onDeleted={() => router.navigate({ to: "/audits" })}
      />
    </div>
  );

  if (!review || (review.status !== "complete" && review.status !== "no_applicable_rules")) {
    return (
      <div>
        {topBar}
        <DocumentTabs currentDocumentId={personDocument.id} siblings={siblings} />
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{personDocument.client_name ?? "Unresolved document"}</h1>
          {personDocument.service_code && <Badge variant="secondary">{personDocument.service_code}</Badge>}
          <span className="text-sm text-muted-foreground">DOS {personDocument.date_of_service}</span>
        </div>
        {review ? (
          <ProcessingStatusCard status={review.status} errorMessage={review.error_message} />
        ) : (
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
        )}
      </div>
    );
  }

  return (
    <div>
      {topBar}
      <DocumentTabs currentDocumentId={personDocument.id} siblings={siblings} />

      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="mr-2 text-2xl font-semibold tracking-tight">{review.client_name ?? "Unresolved document"}</h1>
          <AuditFlagsBadge result={review.audit_result} />
          {review.status === "no_applicable_rules" && (
            <Badge variant="secondary" className="bg-muted text-muted-foreground">No rules apply</Badge>
          )}
          {!personDocument.active && <Badge variant="secondary" className="bg-muted text-muted-foreground">Archived</Badge>}
        </div>
        {/* Extraction detail is one click away, not a full field row taking
            up space by default — see this phase's own report. */}
        <div className="flex flex-wrap items-center gap-2">
          {previousSessionNote && (
            <Button asChild variant="outline" size="sm">
              <Link to="/audits/$auditId" params={{ auditId: previousSessionNote.id }}>
                <FileText className="mr-1.5 h-4 w-4" />
                View Previous Session Note
              </Link>
            </Button>
          )}
          {review.has_activity_statement && (
            <Button asChild variant="outline" size="sm">
              <a href={personDocumentActivityStatementPdfUrl(personDocument.id)} target="_blank" rel="noreferrer">
                <Clock className="mr-1.5 h-4 w-4" />
                View Activity Statement
              </a>
            </Button>
          )}
          <Button asChild variant="outline" size="sm">
            <Link to="/audits/$auditId/extraction" params={{ auditId: personDocument.id }}>
              <FileSearch className="mr-1.5 h-4 w-4" />
              View Full Extraction
            </Link>
          </Button>
          {review.status === "complete" && (
            <Button variant="outline" size="sm" onClick={handleDownloadCsv}>
              <Download className="mr-1.5 h-4 w-4" />
              Download CSV
            </Button>
          )}
        </div>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-5 lg:grid-cols-9">
        <HeaderChip label="Client" value={review.client_name ?? "Unresolved"} />
        <HeaderChip label="Provider" value={review.provider_name ?? "—"} />
        <HeaderChip label="BCBA" value={review.bcba_name ?? "—"} />
        <HeaderChip label="Code" value={review.service_code ?? "—"} />
        <HeaderChip label="Date of Service" value={review.date_of_service ?? "—"} />
        <HeaderChip label="Start Time" value={review.session_start_time ?? "—"} />
        <HeaderChip label="End Time" value={review.session_end_time ?? "—"} />
        <HeaderChip label="Score" value={review.score !== null ? `${review.score}%` : "—"} />
        <HeaderChip label="Reviewed By" value={review.reviewed_by ?? "—"} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Card className={`shadow-card ${PANEL_HEIGHT} flex flex-col`}>
          <CardContent className="flex min-h-0 flex-1 flex-col p-0">
            <div className="border-b px-5 py-3 text-sm font-semibold">Document</div>
            <PdfViewer ref={pdfRef} src={personDocumentPdfUrl(personDocument.id)} className="min-h-0 flex-1" />
          </CardContent>
        </Card>

        <div className="flex min-h-0 flex-col gap-4">
          <FindingsPanel
            groupedResults={review.grouped_results}
            onGoToPage={(p) => pdfRef.current?.goToPage(p)}
            emptyMessage={
              review.status === "no_applicable_rules"
                ? `No compliance rules apply to this document type (${review.service_code ?? "this code"}) yet.`
                : undefined
            }
          />
        </div>
      </div>

      <Card className="mt-4 shadow-card">
        <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
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
        </CardContent>
      </Card>
    </div>
  );
}

/** Each finding row is individually collapsible — own local state, not
 * lifted into the parent list, so expanding one has no effect on any
 * other and composes independently with the group-level accordion above
 * it (matching the reference project's own RuleResultCard: local
 * per-card `expanded` state, never a second accordion). Collapsed by
 * default: just the bold question. Expanded reveals the humanized
 * answer/evidence summary and any page-jump links. */
function RuleRow({
  rule,
  chipClass,
  onGoToPage,
}: {
  rule: RuleResultRow;
  chipClass: string;
  onGoToPage: (page: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const pages = normalizePages(rule.final_pages);
  const reason = findingReason(rule.final_status, rule.final_finding);

  return (
    <li
      className="cursor-pointer rounded-lg border bg-card p-3"
      onClick={() => setExpanded((e) => !e)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setExpanded((x) => !x);
        }
      }}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-1.5">
          <button
            type="button"
            className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((x) => !x);
            }}
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
          <div className="min-w-0">
            <span className="text-sm font-bold">{rule.question ?? rule.rule_id}</span>
            {/* Always visible, even collapsed — a bare "uncertain"/
                "not_checkable" chip with no context was a real, reported
                gap. Distinguishes "nothing to compare against yet" (a
                missing prior note/timesheet) from genuine judge
                disagreement, since those mean very different things to a
                reviewer. */}
            {reason && <div className="mt-0.5 text-xs font-medium text-muted-foreground">{reason}</div>}
          </div>
        </div>
        <span className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold ${chipClass}`}>
          {rule.final_status}
          {rule.is_overridden ? " (overridden)" : ""}
        </span>
      </div>
      {expanded && (
        <div className="ml-[22px] mt-1.5">
          <p className="text-sm font-normal text-muted-foreground">
            {/* final_finding is now the REAL, already-humanized text (a
                real Haiku rewrite, run once after the full review
                completes — see backend/app/routers/person_documents.py::
                run_review) — no client-side shortening needed any more.
                A pre-humanize-pass row (predates this change) falls back
                to its own raw text, same field, nothing special to do. */}
            {rule.final_finding}
          </p>
          {pages.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-2">
              {pages.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onGoToPage(p);
                  }}
                  className="text-[11px] font-medium text-primary hover:underline"
                  title={`Jumps to page ${p} in the document`}
                >
                  [Page {p}]
                </button>
              ))}
            </div>
          )}
          <p className="mt-1 text-xs text-muted-foreground/70">{rule.rule_id}</p>
        </div>
      )}
    </li>
  );
}
