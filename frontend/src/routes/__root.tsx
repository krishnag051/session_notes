import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  HeadContent,
} from "@tanstack/react-router";
import { useEffect } from "react";

import { reportLovableError } from "../lib/lovable-error-reporting";
import { AppSidebar } from "@/components/AppSidebar";
import { DemoLoginGate } from "@/components/DemoLoginGate";
import { Toaster } from "@/components/ui/sonner";


function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Go home
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();
  useEffect(() => {
    reportLovableError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          This page didn't load
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Something went wrong on our end. You can try refreshing or head back home.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Try again
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            Go home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { title: "Session Note Compliance — MasterFaster" },
      {
        name: "description",
        content: "Audit ABA session notes against compliance rules.",
      },
    ],
  }),

  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootComponent() {
  const { queryClient } = Route.useRouteContext();

  return (
    <QueryClientProvider client={queryClient}>
      <HeadContent />
      {/* Demo-only screen lock (frontend-only, no backend enforcement) —
          see DemoLoginGate.tsx's own top comment. Gates EVERYTHING below,
          including the sidebar, until the hardcoded demo credentials are
          entered once (persisted in localStorage across browser restarts). */}
      <DemoLoginGate>
        <div className="min-h-screen bg-background">
          <AppSidebar />
          <main className="md:pl-64">
            {/* Widened from max-w-7xl (1280px) -- the Audits table (14
                columns once Upload Time is added) was leaving real,
                visible unused space on both sides at the old width. This
                is a global change (every page shares this one wrapper),
                not an Audits-only override -- deliberate, since a wider
                content area only ever gives a data-dense admin tool more
                room, and no other page here has fixed-width content that
                would look wrong with extra side margin reclaimed. */}
            <div className="mx-auto max-w-[1600px] px-4 py-8">
              {/* Required: nested routes render here. Removing <Outlet /> breaks all child routes. */}
              <Outlet />
            </div>
          </main>
          <Toaster />
        </div>
      </DemoLoginGate>
    </QueryClientProvider>
  );
}

