import { useEffect, useState } from "react";

const STORAGE_KEY = "deploycore_theme";
type Theme = "light" | "dark";

function getInitialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return "dark";
}

// Applied once at module load (before React's first paint), not just in the
// hook's effect, so switching pages/reloading never flashes the wrong theme.
document.documentElement.classList.toggle("dark", getInitialTheme() === "dark");

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  function toggle() {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }

  return { theme, toggle };
}
