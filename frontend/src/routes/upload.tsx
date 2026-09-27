import { createFileRoute, Link } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { UploadCloud, FileText, CheckCircle2 } from "lucide-react";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";

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

type Phase = "idle" | "processing" | "done";

function UploadPage() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [fileName, setFileName] = useState("");
  const [progress, setProgress] = useState(0);
  const [finishedAt, setFinishedAt] = useState("");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function start(name: string) {
    setFileName(name);
    setPhase("processing");
    setProgress(8);
    const timer = setInterval(() => {
      setProgress((p) => {
        if (p >= 95) {
          clearInterval(timer);
          return 100;
        }
        return p + 9;
      });
    }, 220);
    setTimeout(() => {
      clearInterval(timer);
      setProgress(100);
      setFinishedAt(new Date().toLocaleString());
      setPhase("done");
    }, 2800);
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
                start(f ? f.name : "session-notes-batch.pdf");
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
                  start(f ? f.name : "session-notes-batch.pdf");
                }}
              />
            </div>
          )}

          {phase === "processing" && (
            <div className="flex flex-col items-center px-6 py-20 text-center">
              <FileText className="h-10 w-10 animate-pulse text-primary" />
              <div className="mt-4 text-base font-semibold">Processing upload… classifying documents</div>
              <p className="mt-1 text-sm text-muted-foreground">{fileName}</p>
              <Progress value={progress} className="mt-6 w-full max-w-md" />
            </div>
          )}

          {phase === "done" && (
            <div className="flex flex-col items-center px-6 py-20 text-center">
              <CheckCircle2 className="h-10 w-10 text-success" />
              <div className="mt-4 text-base font-semibold">Upload complete — 6 patients processed</div>
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
        </CardContent>
      </Card>
    </div>
  );
}
