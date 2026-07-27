import { useEffect, useState } from "react";

type ApiState = "checking" | "ready" | "unavailable";

function App() {
  const [apiState, setApiState] = useState<ApiState>("checking");

  useEffect(() => {
    const controller = new AbortController();

    async function checkApi() {
      try {
        const response = await fetch("/api/health/ready", {
          signal: controller.signal,
        });
        setApiState(response.ok ? "ready" : "unavailable");
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setApiState("unavailable");
      }
    }

    void checkApi();

    return () => controller.abort();
  }, []);

  return (
    <main className="shell">
      <section className="hero" aria-labelledby="dashboard-title">
        <p className="eyebrow">Local development foundation</p>
        <h1 id="dashboard-title">Formula1 Dashboard</h1>
        <p className="summary">
          The API, worker, database, and frontend scaffold are connected. Race
          data ingestion has not been implemented yet.
        </p>

        <div className="status-card" aria-live="polite">
          <span className={`status-dot status-dot--${apiState}`} aria-hidden="true" />
          <div>
            <span className="status-label">Backend readiness</span>
            <strong>{apiState.replace("_", " ")}</strong>
          </div>
        </div>
      </section>

      <section className="scope" aria-labelledby="scope-title">
        <h2 id="scope-title">Current scope</h2>
        <ul>
          <li>FastAPI health endpoints</li>
          <li>PostgreSQL connectivity</li>
          <li>Independent worker process</li>
          <li>React and Vite development server</li>
        </ul>
      </section>
    </main>
  );
}

export default App;

