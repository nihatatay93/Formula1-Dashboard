import type { ReactNode } from "react";

/**
 * The three states every view can be in, said the same way in each.
 *
 * They were hand-rolled per view before, which produced real inconsistencies
 * rather than only cosmetic ones: some errors announced themselves to a screen
 * reader and some did not, and the loading spinner carried a class named after
 * the one component it started in.
 *
 * Empty and error are deliberately different. "Nothing to show" is a fact
 * about the archive; "we could not load it" is a failure, and a reader must
 * never take one for the other.
 */

export function LoadingState({ children }: { children: ReactNode }) {
  return (
    <div aria-live="polite" className="view-state view-state--loading">
      <span className="view-state__spinner" />
      {children}
    </div>
  );
}

export function ErrorState({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="inline-alert inline-alert--danger" role="alert">
      <strong>{title}</strong>
      <span>{message}</span>
      {onRetry ? (
        <button className="secondary-action" onClick={onRetry} type="button">
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  marker,
  title,
  children,
  action,
}: {
  /** A short glyph or number, never the whole explanation. */
  marker: string | number;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-state__number">{marker}</span>
      <div>
        <h3>{title}</h3>
        <p>{children}</p>
        {action}
      </div>
    </div>
  );
}
