import { downloadCsv, toCsv, type CsvValue } from "./csv";

/**
 * Exports what a card is showing, not what it fetched.
 *
 * The rows are built by the caller from the same state it renders, so a
 * filtered chart exports the filtered set. An export that quietly returned the
 * unfiltered data would disagree with the screen it sits on.
 */
export default function ExportButton({
  filename,
  headers,
  rows,
  label = "Export CSV",
}: {
  filename: string;
  headers: readonly string[];
  rows: readonly CsvValue[][];
  label?: string;
}) {
  const disabled = rows.length === 0;

  return (
    <button
      className="export-button"
      disabled={disabled}
      onClick={() => downloadCsv(filename, toCsv(headers, rows))}
      title={
        disabled ? "There is nothing to export yet" : `Download ${filename}.csv`
      }
      type="button"
    >
      {label}
    </button>
  );
}
