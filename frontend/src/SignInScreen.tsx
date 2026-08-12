import { useEffect, useRef, useState } from "react";

import { ApiClientError, signIn } from "./api";

/**
 * The sign-in screen for a deployment that requires access.
 *
 * The password is held in component state only for as long as it takes to
 * post it: it is never written to storage, never put in a URL, and the field
 * is cleared on a failure so a shoulder-glance or a screenshot does not carry
 * it. The session that results lives in an HttpOnly cookie the page cannot
 * read.
 */

function describe(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.code === "too_many_attempts") {
      // Carries the remaining lockout, so it is shown as the backend wrote it.
      return error.message;
    }
    if (error.status === 401) {
      return "That password was not accepted.";
    }
    return error.message;
  }
  return "The dashboard could not reach the backend.";
}

export default function SignInScreen({
  onSignedIn,
}: {
  onSignedIn: () => void;
}) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const fieldRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    fieldRef.current?.focus();
  }, []);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (pending || password.length === 0) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await signIn(password);
      setPassword("");
      onSignedIn();
    } catch (caught) {
      setError(describe(caught));
      // Never leave a rejected password sitting in the field.
      setPassword("");
      fieldRef.current?.focus();
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="sign-in" aria-labelledby="sign-in-title">
      <form className="sign-in__card" onSubmit={(event) => void handleSubmit(event)}>
        <div className="sign-in__brand">
          <span className="brand__mark" aria-hidden="true">
            F<span>1</span>
          </span>
          <div>
            <p className="section-kicker">Formula One</p>
            <h1 id="sign-in-title">Data platform</h1>
          </div>
        </div>

        <p className="sign-in__blurb">
          This dashboard is private. Sign in to reach the archive and live
          timing.
        </p>

        <label className="sign-in__field" htmlFor="dashboard-password">
          Password
          <input
            autoComplete="current-password"
            disabled={pending}
            id="dashboard-password"
            name="password"
            onChange={(event) => setPassword(event.target.value)}
            ref={fieldRef}
            type="password"
            value={password}
          />
        </label>

        {error ? (
          <p className="inline-alert inline-alert--danger" role="alert">
            {error}
          </p>
        ) : null}

        <button
          className="primary-action"
          disabled={pending || password.length === 0}
          type="submit"
        >
          {pending ? "Signing in…" : "Sign in"}
        </button>

        <p className="sign-in__footnote">
          Everything runs on this deployment. Your F1 TV credentials are
          separate and are never stored here in plain text.
        </p>
      </form>
    </main>
  );
}
