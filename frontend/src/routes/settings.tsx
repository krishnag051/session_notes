import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppSidebar";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/settings")({
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

function SettingsPage() {
  const [org, setOrg] = useState("MasterFaster Inc.");
  const [emailNotifications, setEmailNotifications] = useState(true);
  const [requireReview, setRequireReview] = useState(false);

  return (
    <div>
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
    </div>
  );
}
