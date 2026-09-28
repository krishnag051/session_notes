import { createFileRoute, Link, useRouter } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { UploadCloud, FileText, CheckCircle2, XCircle } from "lucide-react";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { uploadBatch } from "@/lib/api";

export const Route = createFileRoute("/upload")({
  head: () => ({
    meta: [
      { title: "Upload — Session Note Compliance" },
      { name: "description", content: "Upload a session note PDF for automated compliance auditing." },
      { property: "og:title", content: "Upload — Session Note Compliance" },
      { property: "og:description", content: "Upload a session note PDF for automated compliance auditing." },
    ],
  }),
  component: UploadPage,
});

type Phase = "idle" | "processing" | "done" | "error";

function UploadPage() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("idle");
  const [fileName, setFileName] = useState("");
  const [finishedAt, setFinishedAt] = useState("");
  const [peopleCount, setPeopleCount] = useState(0);
  const [errorMessage, setErrorMessage] = useState("");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function start(file: File) {
    setFileName(file.name);
    setPhase("processing");
    try {
      const batch = (await uploadBatch(file)) as { documents: { person_id: string | null }[] };
      const distinctPeople = new Set(batch.documents.map((d) => d.person_id).filter(Boolean));
      setPeopleCount(distinctPeople.size);
      setFinishedAt(new Date().toLocaleString());
      setPhase("done");
      router.invalidate();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
      setPhase("error");
    }
  }

  return (
    <div>
      <PageHeader title="Upload" subtitle="Drop a session note PDF to run it through the compliance rule set." />

      <Card className="shadow-card">
        <CardContent className="p-8">
          {phase === "idle" && (
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                const f = e.dataTransfer.files?.[0];
                if (f) start(f);
              }}
              onClick={() => inputRef.current?.click()}
              className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-20 text-center transition-colors ${
                dragging ? "border-primary bg-accent" : "border-border hover:border-primary/60 hover:bg-muted/50"
              }`}
            >
              <UploadCloud className="h-10 w-10 text-primary" />
              <div className="mt-4 text-base font-semibold">Drop a PDF here</div>
              <p className="mt-1 text-sm text-muted-foreground">or click to browse your files</p>
              <input
                ref={inputRef}
                type="file"
                accept="application/pdf"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) start(f);
                }}
              />
            </div>
          )}

          {phase === "processing" && (
            <div className="flex flex-col items-center px-6 py-20 text-center">
              <FileText className="h-10 w-10 animate-pulse text-primary" />
              <div className="mt-4 text-base font-semibold">Processing upload… classifying documents</div>
              <p className="mt-1 text-sm text-muted-foreground">{fileName}</p>
            </div>
          )}

          {phase === "done" && (
            <div className="flex flex-col items-center px-6 py-20 text-center">
              <CheckCircle2 className="h-10 w-10 text-success" />
              <div className="mt-4 text-base font-semibold">
                Upload complete — {peopleCount} {peopleCount === 1 ? "patient" : "patients"} processed
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                {fileName} · {finishedAt}
              </p>
              <div className="mt-6 flex gap-3">
                <Button asChild>
                  <Link to="/audits">View results in Audits</Link>
                </Button>
                <Button variant="outline" onClick={() => setPhase("idle")}>
                  Upload another
                </Button>
              </div>
            </div>
          )}

          {phase === "error" && (
            <div className="flex flex-col items-center px-6 py-20 text-center">
              <XCircle className="h-10 w-10 text-destructive" />
              <div className="mt-4 text-base font-semibold">Upload failed</div>
              <p className="mt-1 max-w-md text-sm text-muted-foreground">{errorMessage}</p>
              <Button variant="outline" className="mt-6" onClick={() => setPhase("idle")}>
                Try again
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
