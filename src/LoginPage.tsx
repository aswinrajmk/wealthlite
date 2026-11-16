import { useState, type FormEvent } from "react";
import { login, type User } from "./api";

// DEMO: change this label (for example to "Log in"), push to main and watch the QA dashboard.
// The flow tests self-heal through the test id; the copy test fails and reports a screenshot.
const SIGN_IN_LABEL = "Sign in";

export function LoginPage({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    const result = await login(String(form.get("email") ?? ""), String(form.get("password") ?? ""));
    setBusy(false);
    if (result.ok) onSignedIn(result.data);
    else setError(result.message);
  }

  return (
    <main className="login">
      <div className="login__card">
        <p className="brand">WealthLite</p>
        <h1>Sign in to your portfolio</h1>
        <form onSubmit={submit} aria-label="Sign in" noValidate>
          <label htmlFor="email">Email</label>
          <input id="email" name="email" type="email" autoComplete="username" required data-testid="login-email" />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            data-testid="login-password"
          />
          {error && (
            <p className="login__error" role="alert" data-testid="login-error">
              {error}
            </p>
          )}
          <button type="submit" disabled={busy} data-testid="login-submit">
            {busy ? "Signing in…" : SIGN_IN_LABEL}
          </button>
        </form>
        <div className="login__demo">
          <p>Demo account (synthetic):</p>
          <p>
            <code>demo@wealthlite.test</code> / <code>demo-pass-2026</code>
          </p>
        </div>
      </div>
    </main>
  );
}
