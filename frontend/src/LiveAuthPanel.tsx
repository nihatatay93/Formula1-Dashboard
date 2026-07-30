import { useState } from "react";

import { ApiClientError, clearLiveAuth, storeLiveAuth } from "./api";
import type { LiveAuthStatus } from "./contracts";

/**
 * F1 TV connection panel.
 *
 * Authentication happens in the user's own browser against formula1.com. This
 * panel only accepts the resulting `login-session` cookie, exactly as the FastF1
 * companion extension does, so no password ever reaches the application. The
 * stored token is never sent back to the browser; only its expiry is shown.
 */

function formatRemaining(seconds: number): string {
  if (seconds <= 0) {
    return "expired";
  }
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  if (days > 0) {
    return `${days}d ${hours}h`;
  }
  const minutes = Math.floor((seconds % 3_600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return "The dashboard could not reach the live timing service.";
}

export default function LiveAuthPanel({
  auth,
  onChanged,
}: {
  auth: LiveAuthStatus;
  onChanged: (next: LiveAuthStatus) => void;
}) {
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConnect() {
    setPending(true);
    setError(null);
    try {
      onChanged(await storeLiveAuth(token.trim()));
      // The value is never kept in component state after it is stored.
      setToken("");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  async function handleSignOut() {
    setPending(true);
    setError(null);
    try {
      onChanged(await clearLiveAuth());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="live-auth-title" className="live-auth">
      <div className="live-auth__heading">
        <div>
          <p className="section-kicker">F1 TV account</p>
          <h3 id="live-auth-title">Live feed access</h3>
        </div>
        <span
          className={`live-badge live-badge--${
            auth.authenticated ? "open" : auth.expired ? "closed" : "idle"
          }`}
        >
          {auth.authenticated
            ? "Connected"
            : auth.expired
              ? "Expired"
              : "Not connected"}
        </span>
      </div>

      {auth.authenticated ? (
        <>
          <p className="live-auth__detail">
            Session token valid for {formatRemaining(auth.seconds_remaining)}.
            Expiry taken from{" "}
            {auth.expiry_source === "token_claim"
              ? "the token itself"
              : "the configured lifetime"}
            . You will need to reconnect when it lapses.
          </p>
          <button
            className="secondary-action"
            disabled={pending}
            onClick={() => void handleSignOut()}
            type="button"
          >
            Sign out
          </button>
        </>
      ) : (
        <>
          {auth.expired ? (
            <div className="inline-alert inline-alert--warning" role="status">
              <strong>Your F1 TV session has expired</strong>
              <span>Sign in again to restore live feed access.</span>
            </div>
          ) : null}

          <ol className="live-auth__steps">
            <li>
              Sign in at{" "}
              <a
                href="https://account.formula1.com/"
                rel="noreferrer noopener"
                target="_blank"
              >
                account.formula1.com
              </a>{" "}
              in this browser. An F1 TV subscription is required.
            </li>
            <li>
              Either use the FastF1 companion extension pointed at this
              instance, or copy the <code>login-session</code> cookie for{" "}
              <code>livetiming.formula1.com</code> and paste it below.
            </li>
          </ol>

          <label className="live-auth__field" htmlFor="live-auth-token">
            login-session cookie
            <input
              autoComplete="off"
              id="live-auth-token"
              onChange={(event) => setToken(event.target.value)}
              placeholder="Paste the cookie value"
              spellCheck={false}
              type="password"
              value={token}
            />
          </label>

          <button
            className="primary-action"
            disabled={pending || token.trim().length === 0}
            onClick={() => void handleConnect()}
            type="button"
          >
            {pending ? "Connecting…" : "Connect account"}
          </button>
        </>
      )}

      {error ? (
        <div className="inline-alert inline-alert--danger" role="alert">
          <strong>Could not update F1 TV access</strong>
          <span>{error}</span>
        </div>
      ) : null}

      <p className="live-auth__note">
        Your password is never sent to this application. The token is stored
        only inside this local instance, is never returned to the browser, and
        is never written to a session log.
      </p>
    </section>
  );
}
