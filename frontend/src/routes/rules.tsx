import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Pencil } from "lucide-react";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RULES } from "@/lib/mock-data";

export const Route = createFileRoute("/rules")({
  head: () => ({
    meta: [
      { title: "Rules — Session Note Compliance" },
      { name: "description", content: "The compliance questions applied to 97151, 97153 and 97156 notes." },
      { property: "og:title", content: "Rules — Session Note Compliance" },
      { property: "og:description", content: "The compliance questions applied to 97151, 97153 and 97156 notes." },
    ],
  }),
  component: RulesPage,
});

function RulesPage() {
  const [q, setQ] = useState("");
  const rows = useMemo(
    () => RULES.filter((r) => r.question.toLowerCase().includes(q.toLowerCase())),
    [q],
  );

  return (
    <div>
      <PageHeader title="Rules" subtitle="Compliance questions evaluated against every uploaded note." />

      <Card className="shadow-card">
        <CardContent className="p-0">
          <div className="border-b p-4">
            <Input
              placeholder="Search question text…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              className="max-w-md"
            />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-5 py-3 font-medium">Question</th>
                  <th className="px-5 py-3 font-medium">Applies to</th>
                  <th className="px-5 py-3 font-medium">Type</th>
                  <th className="px-5 py-3 font-medium">Severity</th>
                  <th className="px-5 py-3 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.question} className="border-b last:border-0 hover:bg-muted/50">
                    <td className="max-w-xl px-5 py-3 font-medium">{r.question}</td>
                    <td className="px-5 py-3">
                      <div className="flex flex-wrap gap-1">
                        {r.codes.map((c) => (
                          <Badge key={c} variant="secondary">
                            {c}
                          </Badge>
                        ))}
                      </div>
                    </td>
                    <td className="px-5 py-3 text-muted-foreground">{r.type}</td>
                    <td className="px-5 py-3">
                      <span
                        className={`rounded-md px-2 py-0.5 text-xs font-semibold ${
                          r.severity === "Critical"
                            ? "bg-destructive/12 text-destructive"
                            : r.severity === "High"
                              ? "bg-warning/20 text-warning-foreground"
                              : "bg-muted text-muted-foreground"
                        }`}
                      >
                        {r.severity}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Button size="icon" variant="ghost" aria-label="Edit rule">
                        <Pencil className="h-4 w-4" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
