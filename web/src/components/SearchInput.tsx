"use client";

import { useCallback, type KeyboardEvent } from "react";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  placeholder?: string;
  size?: "default" | "large";
  autoFocus?: boolean;
}

export default function SearchInput({
  value,
  onChange,
  onSubmit,
  placeholder = "Search packages...",
  size = "default",
  autoFocus = false,
}: SearchInputProps) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && onSubmit) {
        onSubmit();
      }
    },
    [onSubmit]
  );

  const sizeClasses =
    size === "large"
      ? "h-14 px-6 text-lg rounded-xl"
      : "h-11 px-4 text-sm rounded-lg";

  return (
    <div className="relative w-full">
      <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4">
        <svg
          className={`text-muted ${size === "large" ? "h-5 w-5" : "h-4 w-4"}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
          />
        </svg>
      </div>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        autoFocus={autoFocus}
        aria-label="Search packages"
        className={`w-full border border-border bg-card text-foreground placeholder-muted transition-colors focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary ${sizeClasses} ${
          size === "large" ? "pl-12" : "pl-10"
        }`}
      />
    </div>
  );
}
