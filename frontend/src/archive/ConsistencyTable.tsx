import type { ConsistencyRow } from "../contracts";

/**
 * How repeatable each driver's race pace was, ranked.
 *
 * The spread chart beside each row is a horizontal span: the interquartile
 * range placed on a shared percentage axis, with the median marked. Reading it
 * across the column shows both who is quick and who is repeatable, which the
 * standard-deviation number alone does not.
 *
 * Every figure is a percentage of the best clean lap of the same session, so a
 * season that mixes Monaco with Monza stays comparable.
 */

const AXIS_FROM = 100;

function formatPercent(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

export default function ConsistencyTable({
  items,
}: {
  items: ConsistencyRow[];
}) {
  const measured = items.filter((row) => row.median_percent !== null);
  // The axis spans the drivers actually drawn, with a little air.
  const axisTo = measured.length
    ? Math.ceil(
        Math.max(
          ...measured.map((row) => (row.median_percent ?? 0) + (row.iqr_percent ?? 0)),
        ),
      ) + 1
    : AXIS_FROM + 1;
  const span = Math.max(axisTo - AXIS_FROM, 1);
  const place = (value: number) =>
    Math.min(Math.max(((value - AXIS_FROM) / span) * 100, 0), 100);

  return (
    <div className="consistency__table-wrap">
      <table className="consistency__table">
        <caption>
          Ranked by standard deviation of clean race laps, most repeatable
          first. Percentages are of the best clean lap of the same session;
          the bar spans the middle half of a driver's laps, and the mark is
          the median.
        </caption>
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">Driver</th>
            <th scope="col">Team</th>
            <th scope="col">Laps</th>
            <th scope="col">Median</th>
            <th scope="col">Std dev</th>
            <th scope="col">IQR</th>
            <th scope="col">Finished</th>
            <th scope="col">Spread</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, index) => {
            const colour = row.team_color_hex ?? "var(--muted-dark)";
            const median = row.median_percent;
            const iqr = row.iqr_percent ?? 0;
            const from = median === null ? null : place(median - iqr / 2);
            const to = median === null ? null : place(median + iqr / 2);

            return (
              <tr key={row.driver_id}>
                <td className="consistency__rank">
                  {row.median_percent === null ? "—" : index + 1}
                </td>
                <td>
                  <div className="consistency__name">
                    <span
                      aria-hidden="true"
                      className="consistency__swatch"
                      style={{ background: colour }}
                    />
                    <div>
                      <strong>{row.display_name}</strong>
                      {row.abbreviation ? <small>{row.abbreviation}</small> : null}
                    </div>
                  </div>
                </td>
                <td className="consistency__team">{row.team_name ?? "—"}</td>
                <td className="consistency__figure">{row.clean_laps}</td>
                <td className="consistency__figure">
                  {formatPercent(row.median_percent)}%
                </td>
                <td className="consistency__figure consistency__figure--lead">
                  {formatPercent(row.std_dev_percent)}
                </td>
                <td className="consistency__figure">
                  {formatPercent(row.iqr_percent)}
                </td>
                <td className="consistency__figure">
                  {row.finish_rate === null
                    ? "—"
                    : `${row.races_classified}/${row.races_started}`}
                </td>
                <td>
                  {from === null || to === null ? (
                    <span className="consistency__spread consistency__spread--empty">
                      no clean lap
                    </span>
                  ) : (
                    <span className="consistency__spread">
                      <span
                        className="consistency__range"
                        style={{
                          background: colour,
                          insetInlineStart: `${from}%`,
                          width: `${Math.max(to - from, 1.5)}%`,
                        }}
                      />
                      <span
                        className="consistency__median"
                        style={{ insetInlineStart: `${place(median ?? 0)}%` }}
                      />
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
