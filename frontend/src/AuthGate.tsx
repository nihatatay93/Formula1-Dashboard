import { useCallback, useEffect, useState } from "react";

import { getAuthSession, onUnauthorized } from "./api";
import SignInScreen from "./SignInScreen";
import type { AuthSession } from "./contracts";

/**
 * Decides whether the dashboard is reachable.
 *
 * The state comes from the backend rather than from anything the page stores,
 * because the session lives in an HttpOnly cookie this code cannot read. It
 * also subscribes to the client's unauthorized signal: a session can lapse
 * mid-use, and the dashboard should return to sign-in rather than quietly
 * filling with failed requests.
 */

type Phase = "checking" | "unreachable" | "signed-out" | "signed-in";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");

  const check = useCallback(async (signal?: AbortSignal) => {
    try {
      const session: AuthSession = await getAuthSession(signal);
      if (signal?.aborted) {
        return;
      }
      // A deployment that requires no sign-in reports itself authenticated.
      setPhase(session.authenticated ? "signed-in" : "signed-out");
    } catch {
      if (!signal?.aborted) {
        setPhase("unreachable");
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void check(controller.signal);
    return () => controller.abort();
  }, [check]);

  useEffect(() => {
    onUnauthorized(() => setPhase("signed-out"));
    return () => onUnauthorized(null);
  }, []);

  if (phase === "checking") {
    return (
      <main className="sign-in" aria-live="polite">
        <div className="session-explorer__loading">
          <span />
          Checking your session…
        </div>
      </main>
    );
  }

  if (phase === "unreachable") {
    return (
      <main className="sign-in">
        <div className="sign-in__card">
          <div className="inline-alert inline-alert--danger" role="alert">
            <strong>The backend is not reachable</strong>
            <span>
              The dashboard could not ask whether it needs a sign-in. Check that
              the API is running, then try again.
            </span>
          </div>
          <button
            className="primary-action"
            onClick={() => {
              setPhase("checking");
              void check();
            }}
            type="button"
          >
            Try again
          </button>
        </div>
      </main>
    );
  }

  if (phase === "signed-out") {
    return <SignInScreen onSignedIn={() => void check()} />;
  }

  return <>{children}</>;
}
