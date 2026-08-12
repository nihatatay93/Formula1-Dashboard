import type { SessionEntryResult } from "../contracts";

export default function ResultsTable({
  items,
  selectedEntryId,
  onSelectEntry,
}: {
  items: SessionEntryResult[];
  selectedEntryId: string | null;
  onSelectEntry: (entry: SessionEntryResult) => void;
}) {
  return (
    <div className="results-table-wrap">
      <table className="results-table">
        <thead>
          <tr>
            <th scope="col">Pos</th>
            <th scope="col">Driver</th>
            <th scope="col">Team</th>
            <th scope="col">No.</th>
            <th scope="col">Status</th>
            <th scope="col">Laps</th>
            <th scope="col">Points</th>
            <th scope="col">
              <span className="sr-only">Lap action</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((entry) => (
            <tr
              className={
                selectedEntryId === entry.session_entry_id
                  ? "results-table__selected"
                  : undefined
              }
              key={entry.session_entry_id}
            >
              <td className="results-table__position">
                {entry.result?.classified_position ??
                  entry.result?.position ??
                  "—"}
              </td>
              <td>
                <div className="driver-cell">
                  <span
                    aria-hidden="true"
                    style={{
                      backgroundColor: entry.team_color_hex ?? "#667085",
                    }}
                  />
                  <div>
                    <strong>{entry.display_name}</strong>
                    <small>{entry.abbreviation ?? "—"}</small>
                  </div>
                </div>
              </td>
              <td>{entry.team_name ?? "Independent"}</td>
              <td>{entry.racing_number ?? "—"}</td>
              <td>{entry.result?.status ?? "No classification"}</td>
              <td>{entry.result?.laps_completed ?? "—"}</td>
              <td>{entry.result?.points ?? "—"}</td>
              <td>
                <button
                  aria-pressed={selectedEntryId === entry.session_entry_id}
                  className="lap-action"
                  onClick={() => onSelectEntry(entry)}
                  type="button"
                >
                  {selectedEntryId === entry.session_entry_id
                    ? "Viewing"
                    : "View laps"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}