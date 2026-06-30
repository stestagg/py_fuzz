import { Classes } from "@blueprintjs/core";
import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

function initialTheme(): Theme {
  const saved = window.localStorage.getItem("pfui-theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => {
    document.body.classList.toggle(Classes.DARK, theme === "dark");
    window.localStorage.setItem("pfui-theme", theme);
  }, [theme]);
  return { theme, toggleTheme: () => setTheme((current) => current === "dark" ? "light" : "dark") };
}
