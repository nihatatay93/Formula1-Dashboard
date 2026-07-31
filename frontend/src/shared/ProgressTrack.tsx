/**
 * Segmented ingestion progress: completed, running, pending and failed.
 *
 * The bar is labelled and each segment carries a title, because a bare
 * stack of coloured widths tells a reader the shape without the numbers.
 */
export default function ProgressTrack({
  completed,
  failed,
  pending,
  running,
  total,
}: {
  completed: number;
  failed: number;
  pending: number;
  running: number;
  total: number;
}) {
  const segments = [
    { className: "progress-track__completed", label: "Completed", value: completed },
    { className: "progress-track__running", label: "Running", value: running },
    { className: "progress-track__pending", label: "Pending", value: pending },
    { className: "progress-track__failed", label: "Failed", value: failed },
  ];
  const safeTotal = Math.max(total, 1);

  return (
    <>
      <div
        className={`progress-track${total === 0 ? " progress-track--empty" : ""}`}
        aria-label={
          total === 0
            ? "No sessions discovered"
            : `${completed} of ${total} sessions completed`
        }
        role="img"
      >
        {segments.map((segment) =>
          segment.value > 0 ? (
            <span
              className={segment.className}
              key={segment.label}
              style={{ width: `${(segment.value / safeTotal) * 100}%` }}
              title={`${segment.label}: ${segment.value}`}
            />
          ) : null,
        )}
      </div>
      <div className="progress-legend" aria-hidden="true">
        {segments.map((segment) => (
          <span key={segment.label}>
            <i className={segment.className} />
            {segment.value} {segment.label.toLowerCase()}
          </span>
        ))}
      </div>
    </>
  );
}
