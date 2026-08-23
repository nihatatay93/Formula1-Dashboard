import type { LiveBoard } from "../contracts";

/**
 * Track conditions as one band above the board.
 *
 * The feed sends these as raw strings with no units, keyed by an internal
 * name. A reader wants "TRACK 33.4 °C", not "TrackTemp: 33.4", so each known
 * key is given a short label and its unit here. Anything unrecognised is still
 * shown, because a silently dropped reading is worse than an ugly one.
 */

const READINGS: { key: string; label: string; unit: string }[] = [
  { key: "AirTemp", label: "Air", unit: "°C" },
  { key: "TrackTemp", label: "Track", unit: "°C" },
  { key: "Humidity", label: "Humidity", unit: "%" },
  { key: "WindSpeed", label: "Wind", unit: " m/s" },
  { key: "Pressure", label: "Pressure", unit: " hPa" },
  { key: "Rainfall", label: "Rain", unit: "" },
];

/** The feed sends "1" or "0" rather than a depth. */
function rainfall(value: string): string {
  return value.trim() === "1" ? "yes" : "no";
}

export default function LiveConditions({
  weather,
}: {
  weather: LiveBoard["weather"];
}) {
  const entries = weather ?? {};
  const known = READINGS.filter(({ key }) => entries[key] !== undefined);
  const extra = Object.keys(entries).filter(
    (key) => !READINGS.some((reading) => reading.key === key),
  );

  if (known.length === 0 && extra.length === 0) {
    return null;
  }

  return (
    <dl aria-label="Track conditions" className="live-conditions">
      {known.map(({ key, label, unit }) => (
        <div key={key}>
          <dt>{label}</dt>
          <dd>
            {key === "Rainfall"
              ? rainfall(entries[key])
              : `${entries[key]}${unit}`}
          </dd>
        </div>
      ))}
      {extra.map((key) => (
        <div key={key}>
          <dt>{key.replace(/([a-z])([A-Z])/g, "$1 $2")}</dt>
          <dd>{entries[key]}</dd>
        </div>
      ))}
    </dl>
  );
}
