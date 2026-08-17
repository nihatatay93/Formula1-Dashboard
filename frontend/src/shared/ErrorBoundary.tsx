import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Keeps one broken view from blanking the whole page.
 *
 * A render error anywhere below unmounts React's entire tree, which is how a
 * single unguarded field once took the dashboard down: the standings preview
 * read `items.length` on a response that had no `items`, and the landing page
 * went white. The rail and the shell are still usable when a view fails, so
 * the boundary sits around the view rather than around the application.
 *
 * `resetKey` clears the error when the user navigates: without it a failed
 * view would stay failed even after switching away and back.
 *
 * This has to be a class. Error boundaries are the one thing React still
 * offers no hook for.
 */

interface Props {
  children: ReactNode;
  /** Changing this discards the current error and retries the children. */
  resetKey?: string | number;
  /** What failed, named for the reader. */
  label?: string;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(previous: Props): void {
    if (previous.resetKey !== this.props.resetKey && this.state.error !== null) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // There is no error reporting service in this deployment, so the console
    // is the only record. Keeping the component stack matters: the message
    // alone rarely identifies which view failed.
    console.error("View render failed", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) {
      return this.props.children;
    }

    return (
      <div className="view-error" role="alert">
        <div>
          <h3>
            {this.props.label ?? "This view"} could not be displayed
          </h3>
          <p>
            Something went wrong rendering it. The rest of the dashboard is
            still usable, and choosing another view will try again.
          </p>
          <p className="view-error__detail">{error.message}</p>
          <button
            className="secondary-action"
            onClick={() => this.setState({ error: null })}
            type="button"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
}
