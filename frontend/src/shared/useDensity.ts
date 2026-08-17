import { useEffect, useState } from "react";

/**
 * Table density, remembered between visits.
 *
 * Applied as an attribute on the document root rather than passed down: every
 * table in the app should agree, and threading a prop through seven views to
 * say the same thing would be worse. The stylesheet reads the attribute.
 */

export type Density = "comfortable" | "compact";

const STORAGE_KEY = "f1dashboard.density";

function stored(): Density {
  try {
    return localStorage.getItem(STORAGE_KEY) === "compact"
      ? "compact"
      : "comfortable";
  } catch {
    // Private browsing can refuse storage entirely. A preference is not worth
    // failing the render over.
    return "comfortable";
  }
}

export function useDensity(): [Density, (next: Density) => void] {
  const [density, setDensity] = useState<Density>(stored);

  useEffect(() => {
    document.documentElement.dataset.density = density;
    try {
      localStorage.setItem(STORAGE_KEY, density);
    } catch {
      // Ignored for the same reason as above.
    }
  }, [density]);

  return [density, setDensity];
}
