import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { createReviewer, listReviewers, type Reviewer } from "@/lib/api";

export const Route = createFileRoute("/settings")({
  loader: () => listReviewers(),
  head: () => ({
    meta: [
      { title: "Settings — Session Note Compliance" },
      { name: "description", content: "Organization and review workflow preferences." },
      { property: "og:title", content: "Settings — Session Note Compliance" },
      { property: "og:description", content: "Organization and review workflow preferences." },
    ],
  }),
  component: SettingsPage,
});

function ReviewersSettings() {
  const initialReviewers = Route.useLoaderData();
  const [reviewers, setReviewers] = useState<Reviewer[]>(initialReviewers);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");

  async function addReviewer() {
    if (!name.trim()) return;
    const reviewer = await createReviewer(name.trim(), email.trim() || undefined);
    setReviewers((prev) => (prev.some((r) => r.id === reviewer.id) ? prev : [...prev, reviewer].sort((a, b) => a.name.localeCompare(b.name))));
    setName("");
    setEmail("");
    toast.success(`${reviewer.name} added`);
  }

  return (
    <Card className="max-w-2xl shadow-card">
      <CardContent className="space-y-4 p-6">
        <div>
          <div className="text-sm font-medium">Reviewers</div>
          <p className="text-sm text-muted-foreground">
            No login required — this is just the list that autocompletes on the "Reviewed By" field in each audit.
            Typing a new name there also adds it here automatically.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          {reviewers.map((r) => (
            <span key={r.id} className="rounded-full bg-muted px-3 py-1 text-sm">
              {r.name}
            </span>
          ))}
          {reviewers.length === 0 && <span className="text-sm text-muted-foreground">No reviewers yet.</span>}
        </div>

        <div className="flex flex-wrap items-end gap-3 border-t pt-4">
          <div className="space-y-1">
            <Label htmlFor="reviewer-name">Name</Label>
            <Input id="reviewer-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Doe" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="reviewer-email">Email (optional)</Label>
            <Input id="reviewer-email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="jane@example.com" />
          </div>
          <Button onClick={addReviewer} disabled={!name.trim()}>
            + Add reviewer
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function SettingsPage() {
  const [org, setOrg] = useState("MasterFaster Inc.");
  const [emailNotifications, setEmailNotifications] = useState(true);
  const [requireReview, setRequireReview] = useState(false);

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" subtitle="Admin configuration for this compliance workspace." />

      <Card className="max-w-2xl shadow-card">
        <CardContent className="space-y-6 p-6">
          <div className="space-y-2">
            <Label htmlFor="org">Organization name</Label>
            <Input id="org" value={org} onChange={(e) => setOrg(e.target.value)} />
          </div>

          <div className="flex items-center justify-between border-t pt-5">
            <div>
              <div className="text-sm font-medium">Email notifications on new upload</div>
              <p className="text-sm text-muted-foreground">Notify reviewers when a batch finishes processing.</p>
            </div>
            <Switch checked={emailNotifications} onCheckedChange={setEmailNotifications} />
          </div>

          <div className="flex items-center justify-between border-t pt-5">
            <div>
              <div className="text-sm font-medium">Require review before marking complete</div>
              <p className="text-sm text-muted-foreground">Audits must be signed off by a reviewer.</p>
            </div>
            <Switch checked={requireReview} onCheckedChange={setRequireReview} />
          </div>

          <div className="border-t pt-5">
            <Button onClick={() => toast.success("Settings saved")}>Save</Button>
          </div>
        </CardContent>
      </Card>

      <ReviewersSettings />
    </div>
  );
}
