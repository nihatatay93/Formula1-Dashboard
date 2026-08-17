import { describe, expect, it } from "vitest";

import { toCsv } from "./csv";

describe("toCsv", () => {
  it("writes a header row and one row per record", () => {
    expect(toCsv(["driver", "points"], [["Norris", 128]])).toBe(
      "driver,points\r\nNorris,128",
    );
  });

  it("quotes a field containing a comma", () => {
    // "Verstappen, Max" would otherwise become two columns and shift every
    // value after it.
    expect(toCsv(["driver"], [["Verstappen, Max"]])).toBe(
      'driver\r\n"Verstappen, Max"',
    );
  });

  it("doubles a quote inside a field", () => {
    expect(toCsv(["note"], [['he said "no"']])).toBe(
      'note\r\n"he said ""no"""',
    );
  });

  it("quotes a field containing a newline", () => {
    expect(toCsv(["note"], [["two\nlines"]])).toBe('note\r\n"two\nlines"');
  });

  it("writes an empty field for a missing value", () => {
    // A null median is not the same as a zero one.
    expect(toCsv(["a", "b", "c"], [[null, undefined, 0]])).toBe("a,b,c\r\n,,0");
  });

  it("writes only the header when there are no rows", () => {
    expect(toCsv(["driver"], [])).toBe("driver");
  });
});
