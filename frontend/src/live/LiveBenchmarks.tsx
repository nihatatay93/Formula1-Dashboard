import type { LiveBenchmarks } from "../contracts";

/**
 * The quickest sector times of the session, and their sum.
 *
 * The sum is a lap nobody drove: three sectors from up to three drivers. It is
 * labelled theoretical rather than presented beside real lap times, because a
 * reader who takes it for a lap time would be reading a fiction.
 */
export default function LiveBenchmarksPanel({
  benchmarks,
}: {
  benchmarks: LiveBenchmarks;
}) {
  return (
    <section className="live-board__panel">
      <h4>Best sectors</h4>
      <ol className="live-benchmarks">
        {benchmarks.sectors.map((cell) => (
          <li key={cell.sector}>
            <span className="live-benchmarks__sector">S{cell.sector}</span>
            <span className="live-benchmarks__driver">
              {cell.tla || cell.racing_number}
            </span>
            <span className="live-benchmarks__value">{cell.value}</span>
          </li>
        ))}
      </ol>
      {benchmarks.theoretical_best ? (
        <p className="live-benchmarks__total">
          Theoretical best <strong>{benchmarks.theoretical_best}</strong>
          <small>
            The three quickest sectors added together. No car has driven it.
          </small>
        </p>
      ) : null}
    </section>
  );
}
