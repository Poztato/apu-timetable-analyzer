import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

export interface DashboardOption {
  value: string;
  label: string;
  meta?: string;
}

function normalize(value: string): string {
  return value.toLocaleLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export function DashboardSelect({
  label,
  value,
  options,
  placeholder,
  helper,
  disabled = false,
  searchable = false,
  onChange,
}: {
  label: string;
  value: string;
  options: DashboardOption[];
  placeholder: string;
  helper?: string;
  disabled?: boolean;
  searchable?: boolean;
  onChange: (value: string) => void;
}) {
  const id = useId().replaceAll(":", "");
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const selected = options.find((option) => option.value === value) ?? null;
  const normalizedQuery = normalize(query);
  const visibleOptions = useMemo(
    () =>
      normalizedQuery
        ? options.filter((option) =>
            normalize(`${option.label} ${option.meta ?? ""}`).includes(
              normalizedQuery,
            ),
          )
        : options,
    [normalizedQuery, options],
  );

  useEffect(() => {
    function closeFromOutside(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeFromOutside);
    return () => document.removeEventListener("pointerdown", closeFromOutside);
  }, []);

  useEffect(() => {
    if (!open) {
      setQuery("");
      return;
    }
    const selectedIndex = Math.max(
      0,
      visibleOptions.findIndex((option) => option.value === value),
    );
    setActiveIndex(selectedIndex);
    if (searchable) searchRef.current?.focus();
  }, [open, searchable, value]);

  useEffect(() => {
    if (activeIndex >= visibleOptions.length) setActiveIndex(0);
  }, [activeIndex, visibleOptions.length]);

  function selectOption(option: DashboardOption) {
    onChange(option.value);
    setOpen(false);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => {
        if (visibleOptions.length === 0) return 0;
        return (current + direction + visibleOptions.length) % visibleOptions.length;
      });
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
      } else if (visibleOptions[activeIndex]) {
        selectOption(visibleOptions[activeIndex]);
      }
      return;
    }
    if (event.key === "Home" && open) {
      event.preventDefault();
      setActiveIndex(0);
    }
    if (event.key === "End" && open) {
      event.preventDefault();
      setActiveIndex(Math.max(0, visibleOptions.length - 1));
    }
  }

  return (
    <div
      className={`db-select ${open ? "is-open" : ""} ${disabled ? "is-disabled" : ""}`}
      ref={rootRef}
    >
      <span className="db-field-label" id={`${id}-label`}>
        {label}
      </span>
      <button
        className="db-select-trigger"
        type="button"
        role="combobox"
        aria-label={label}
        aria-controls={`${id}-listbox`}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-activedescendant={
          open && visibleOptions[activeIndex]
            ? `${id}-option-${activeIndex}`
            : undefined
        }
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={handleKeyDown}
      >
        <span className={selected ? "" : "is-placeholder"}>
          {selected?.label ?? placeholder}
        </span>
        <i aria-hidden="true">⌄</i>
      </button>
      {helper && <small className="db-field-helper">{helper}</small>}
      {open && (
        <div className="db-select-popover">
          {searchable && (
            <div className="db-select-search">
              <span aria-hidden="true">⌕</span>
              <input
                ref={searchRef}
                type="search"
                value={query}
                aria-label={`Search ${label} options`}
                placeholder={`Find ${label.toLowerCase()}`}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setOpen(false);
                  }
                }}
              />
            </div>
          )}
          <ul id={`${id}-listbox`} role="listbox" aria-labelledby={`${id}-label`}>
            {visibleOptions.length === 0 ? (
              <li className="db-select-empty">No matching options</li>
            ) : (
              visibleOptions.map((option, index) => (
                <li
                  id={`${id}-option-${index}`}
                  className={`${option.value === value ? "is-selected" : ""} ${index === activeIndex ? "is-active" : ""}`}
                  role="option"
                  aria-selected={option.value === value}
                  key={option.value || "all"}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => selectOption(option)}
                >
                  <span>{option.label}</span>
                  {option.meta && <small>{option.meta}</small>}
                  {option.value === value && <i aria-hidden="true">✓</i>}
                </li>
              ))
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
