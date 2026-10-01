import { Link } from "@tanstack/react-router";
import {
  LayoutDashboard,
  UploadCloud,
  ClipboardCheck,
  ListChecks,
  History,
  Settings as SettingsIcon,
} from "lucide-react";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { to: "/upload", label: "Upload", icon: UploadCloud },
  { to: "/audits", label: "Audits", icon: ClipboardCheck },
  { to: "/upload-history", label: "Upload History", icon: History },
  { to: "/rules", label: "Rules", icon: ListChecks },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
] as const;

export function AppSidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 z-20 hidden w-64 flex-col bg-sidebar text-sidebar-foreground md:flex">
      <div className="flex items-center gap-2.5 px-6 py-6">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-sidebar-primary text-sm font-bold text-sidebar-primary-foreground">
          MF
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-sidebar-accent-foreground">MasterFaster</div>
          <div className="text-xs text-sidebar-foreground/60">Note Compliance</div>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3">
        {NAV.map(({ to, label, icon: Icon, ...rest }) => (
          <Link
            key={to}
            to={to}
            activeOptions={{ exact: "exact" in rest ? rest.exact : false }}
            className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-sidebar-foreground/80 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[status=active]:bg-sidebar-primary data-[status=active]:text-sidebar-primary-foreground"
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        ))}
      </nav>

      <div className="border-t border-sidebar-border px-5 py-4">
        <div className="text-xs text-sidebar-foreground/60">No login required — see Settings for reviewers.</div>
      </div>
    </aside>
  );
}

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
      {subtitle ? <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p> : null}
    </div>
  );
}
