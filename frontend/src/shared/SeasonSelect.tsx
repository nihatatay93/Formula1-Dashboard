import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";


/**
 * Listbox-backed season picker. The native <select> rendered an OS-styled
 * popup that could not be themed with the rest of the dashboard, so this
 * follows the ARIA combobox pattern instead: focus stays on the trigger and
 * the active option is tracked with aria-activedescendant.
 */
export default function SeasonSelect({
  disabled,
  id,
  labelId,
  onChange,
  options,
  value,
}: {
  disabled: boolean;
  id: string;
  labelId: string;
  onChange: (year: number) => void;
  options: number[];
  value: number;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(() =>
    Math.max(0, options.indexOf(value)),
  );
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    function handlePointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    return () =>
      document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  function commit(index: number) {
    const year = options[index];
    if (year !== undefined) {
      onChange(year);
    }
    setOpen(false);
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (disabled) {
      return;
    }
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        const step = event.key === "ArrowDown" ? 1 : -1;
        setActiveIndex((current) =>
          Math.min(Math.max(current + step, 0), options.length - 1),
        );
        return;
      }
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        return;
      case "End":
        event.preventDefault();
        setActiveIndex(options.length - 1);
        return;
      case "Enter":
      case " ":
        event.preventDefault();
        if (open) {
          commit(activeIndex);
        } else {
          setOpen(true);
        }
        return;
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
        }
        return;
      default:
    }
  }

  return (
    <div
      className={`season-select${open ? " season-select--open" : ""}`}
      ref={containerRef}
    >
      <button
        aria-activedescendant={open ? `${id}-option-${activeIndex}` : undefined}
        aria-controls={`${id}-listbox`}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-labelledby={`${labelId} ${id}`}
        className="season-select__trigger"
        disabled={disabled}
        id={id}
        onClick={() => {
          setActiveIndex(Math.max(0, options.indexOf(value)));
          setOpen((current) => !current);
        }}
        onKeyDown={handleKeyDown}
        role="combobox"
        type="button"
      >
        <span>{value}</span>
        <span aria-hidden="true">⌄</span>
      </button>

      {open ? (
        <ul
          aria-labelledby={labelId}
          className="season-select__list"
          id={`${id}-listbox`}
          role="listbox"
        >
          {options.map((year, index) => (
            <li
              aria-selected={year === value}
              className={
                index === activeIndex ? "season-select__option--active" : undefined
              }
              id={`${id}-option-${index}`}
              key={year}
              onClick={() => commit(index)}
              onPointerMove={() => setActiveIndex(index)}
              role="option"
            >
              {year}
              {year === value ? <span aria-hidden="true">✓</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}