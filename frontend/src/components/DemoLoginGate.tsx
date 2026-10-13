import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// DEMO-ONLY SCREEN LOCK — NOT REAL AUTHENTICATION.
//
// This is a cosmetic gate for demo purposes only, requested explicitly as
// "not a real authentication system, no backend call involved":
// - The credentials below are hardcoded and checked entirely client-side —
//   they ship in the compiled frontend bundle, readable by anyone who opens
//   browser devtools or views the built JS. This is NOT a secret.
// - There is NO backend enforcement of any kind. Every API endpoint this
//   app calls remains fully reachable directly (e.g. via curl, or any HTTP
//   client) by anyone who knows or guesses the URL, with or without ever
//   seeing this screen. This gate only hides the UI, not the data.
// - This is a known, accepted tradeoff for this round, not a bug — but it
//   must never be mistaken for real access control, especially given this
//   app handles real client health-compliance data. A real deployment
//   serving real patients needs real backend-enforced authentication
//   before this can be considered any kind of actual security boundary.
// ---------------------------------------------------------------------------

const DEMO_EMAIL = "admin@masterfaster.org";
const DEMO_PASSWORD = "session@123";

// localStorage (not sessionStorage) is deliberate — Krishna wants this to
// persist across full browser restarts, not just the current tab/session,
// so reviewers aren't re-prompted every time they reopen the app.
const STORAGE_KEY = "demo_auth_v1";

function isAuthenticated(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    // Private browsing / blocked storage — fail open to "not authenticated"
    // (show the gate) rather than throwing and blanking the whole app.
    return false;
  }
}

function setAuthenticated(): void {
  try {
    localStorage.setItem(STORAGE_KEY, "true");
  } catch {
    // Storage unavailable — the gate will just re-prompt next load, which
    // is a safe, non-broken fallback, not worth surfacing as an error here.
  }
}

function DemoLoginModal({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState(false);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (email === DEMO_EMAIL && password === DEMO_PASSWORD) {
      setAuthenticated();
      onSuccess();
    } else {
      // Deliberately generic — never reveals which field was wrong.
      setError(true);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 backdrop-blur-sm">
      <Card className="w-full max-w-sm shadow-card">
        <CardContent className="p-6">
          <h1 className="text-lg font-semibold text-foreground">Session Note Compliance</h1>
          <p className="mt-1 text-sm text-muted-foreground">Sign in to continue.</p>
          <form className="mt-5 space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="demo-login-email">Email</Label>
              <Input
                id="demo-login-email"
                type="email"
                autoFocus
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  setError(false);
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="demo-login-password">Password</Label>
              <div className="relative">
                <Input
                  id="demo-login-password"
                  type={passwordVisible ? "text" : "password"}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    setError(false);
                  }}
                  className="pr-9"
                />
                <button
                  type="button"
                  onClick={() => setPasswordVisible((v) => !v)}
                  className="absolute inset-y-0 right-0 flex items-center px-2.5 text-muted-foreground hover:text-foreground"
                  aria-label={passwordVisible ? "Hide password" : "Show password"}
                  tabIndex={-1}
                >
                  {passwordVisible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
            {error && <p className="text-sm text-destructive">Incorrect email or password.</p>}
            <Button type="submit" className="w-full">Sign in</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

/** Wraps the entire app — gates all of it behind the demo login above until
 * `isAuthenticated()` is true, either from a prior successful login
 * (persisted in localStorage, survives a full browser restart) or a fresh
 * one just entered. See this file's own top comment for why this is
 * explicitly NOT real access control. */
export function DemoLoginGate({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(isAuthenticated);

  useEffect(() => {
    // Re-check once on mount, same as the lazy useState initializer above,
    // in case storage genuinely wasn't readable yet at module-eval time —
    // belt-and-suspenders, not expected to change this result in practice.
    setAuthed(isAuthenticated());
  }, []);

  if (!authed) {
    return <DemoLoginModal onSuccess={() => setAuthed(true)} />;
  }
  return <>{children}</>;
}
