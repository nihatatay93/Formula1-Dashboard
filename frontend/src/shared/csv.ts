/**
 * CSV export for the analysis views.
 *
 * CSV rather than PNG: the charts are inline SVG styled by the stylesheet, so
 * serialising one to a canvas loses every colour and rule it inherits — a
 * `fill` of `var(--tyre-medium)` means nothing once the element leaves the
 * document. Exporting the numbers is also what the data is actually for; a
 * picture of a box plot cannot be re-analysed.
 */

export type CsvValue = string | number | null | undefined;

/**
 * Escapes one field. Quotes are doubled, and anything containing a comma,
 * quote or newline is quoted — otherwise a driver named `Verstappen, Max`
 * would silently become two columns.
 */
function escapeField(value: CsvValue): string {
  if (value === null || value === undefined) {
    return "";
  }
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(
  headers: readonly string[],
  rows: readonly CsvValue[][],
): string {
  return [headers, ...rows]
    .map((row) => row.map(escapeField).join(","))
    .join("\r\n");
}

/**
 * Hands the file to the browser.
 *
 * The object URL is revoked on the next frame rather than immediately: Safari
 * has historically cancelled the download if the URL disappears within the
 * same task.
 */
export function downloadCsv(filename: string, content: string): void {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  requestAnimationFrame(() => URL.revokeObjectURL(url));
}
