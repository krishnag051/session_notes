import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, AlertTriangle, FileText } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { PdfViewer } from "@/components/PdfViewer";
import { getExtractionOrNull, getPersonDocumentOrNull, personDocumentPdfUrl } from "@/lib/api";

export const Route = createFileRoute("/audits/$auditId/extraction")({
  loader: async ({ params }) => {
    const [personDocument, extraction] = await Promise.all([
      getPersonDocumentOrNull(params.auditId),
      getExtractionOrNull(params.auditId),
    ]);
    return { personDocument, extraction };
  },
  head: ({ loaderData }) => ({
    meta: [{ title: `${loaderData?.personDocument?.client_name ?? "Unresolved"} — Full extraction` }],
  }),
  component: FullExtractionPage,
});

const FIELD_LABELS: Record<string, string> = {
  session_date: "Session date",
  session_location: "Session location",
  clinician_telehealth_location: "Clinician telehealth location",
  patient_telehealth_location: "Patient telehealth location",
  assessment_activity: "Assessment activity",
  note_detail_level: "Note detail level",
};

const CONFIDENCE_TONE: Record<string, string> = {
  high: "bg-success/12 text-success",
  medium: "bg-warning/20 text-warning-foreground",
  low: "bg-warning/20 text-warning-foreground",
  none: "bg-muted text-muted-foreground",
};

function FullExtractionPage() {
  const { personDocument, extraction } = Route.useLoaderData();

  if (!personDocument || !extraction) {
    return (
      <div>
        <Link to="/audits" className="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" /> Back to audits
        </Link>
        <Card className="shadow-card">
          <CardContent className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
            <AlertTriangle className="h-10 w-10" />
            <div className="text-base font-semibold text-foreground">This audit no longer exists</div>
            <p className="max-w-sm text-sm">It may have been permanently deleted.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div>
      <Link
        to="/audits/$auditId"
        params={{ auditId: personDocument.id }}
        className="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back to audit
      </Link>

      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">
          Full extraction — {personDocument.client_name ?? "Unresolved document"}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Every data point actually pulled from this document and used in matching/checking — for manual
          cross-checking against the source note, not a summary.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Card className="shadow-card">
          <CardContent className="p-0">
            <div className="border-b px-5 py-4 text-sm font-semibold">Document</div>
            <PdfViewer src={personDocumentPdfUrl(personDocument.id)} className="h-[720px]" />
          </CardContent>
        </Card>

        <div className="flex max-h-[760px] flex-col gap-4 overflow-y-auto pr-1">
          <Card className="shadow-card">
            <CardContent className="p-5">
              <div className="mb-3 text-sm font-semibold">Structured fields (session_note_extraction.py)</div>
              {extraction.fields ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  {Object.entries(FIELD_LABELS).map(([key, label]) => {
                    const field = extraction.fields?.[key];
                    return (
                      <div key={key} className="rounded-lg border p-3">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
                          {field && (
                            <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${CONFIDENCE_TONE[field.confidence] ?? ""}`}>
                              {field.confidence}
                            </span>
                          )}
                        </div>
                        <div className="mt-1 font-medium">{field?.value ?? "—"}</div>
                        {field?.source_quote && (
                          <div className="mt-1 text-xs italic text-muted-foreground">"{field.source_quote}"</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Not available.</p>
              )}
            </CardContent>
          </Card>

          <Card className="shadow-card">
            <CardContent className="p-5">
              <div className="mb-3 text-sm font-semibold">
                Goal / data-point bullets ({extraction.data_points?.length ?? 0})
              </div>
              {extraction.data_points && extraction.data_points.length > 0 ? (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <th className="py-2 pr-4 font-medium">Provider</th>
                      <th className="py-2 pr-4 font-medium">Goal</th>
                      <th className="py-2 font-medium">Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {extraction.data_points.map((dp, i) => (
                      <tr key={i} className="border-b last:border-0">
                        <td className="py-2 pr-4">{dp.provider}</td>
                        <td className="py-2 pr-4">{dp.goal}</td>
                        <td className="py-2">{dp.date}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-sm text-muted-foreground">No goal/data-point bullets found in this document.</p>
              )}
            </CardContent>
          </Card>

          <Card className="shadow-card">
            <CardContent className="p-5">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <FileText className="h-4 w-4" /> Full extracted page text
              </div>
              {extraction.full_text ? (
                <pre className="max-h-[400px] overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted/40 p-4 text-xs">
                  {extraction.full_text}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">Not available.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
