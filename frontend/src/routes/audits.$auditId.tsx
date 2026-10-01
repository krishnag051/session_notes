import { createFileRoute, Outlet } from "@tanstack/react-router";

// Pure layout — /audits/$auditId/index.tsx (the actual detail page) and
// /audits/$auditId/extraction.tsx both nest under this. Needed for the
// "View Full Extraction" page to be its own real route rather than being
// silently swallowed by this segment's own leaf component (which has no
// Outlet of its own) — see this phase's own report for the routing bug
// this fixes.
export const Route = createFileRoute("/audits/$auditId")({
  component: () => <Outlet />,
});
