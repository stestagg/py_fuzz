import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "pfui-pinned-files";

function initialPinned(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null");
    return Array.isArray(parsed) ? parsed.filter((name): name is string => typeof name === "string") : [];
  } catch {
    return [];
  }
}

// Pinned filenames persist across artifacts (and sessions) so that, e.g., pinning
// lldb.txt keeps it floated to the top of every artifact that contains that file.
export function usePinnedFiles() {
  const [pinned, setPinned] = useState<string[]>(initialPinned);
  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(pinned));
  }, [pinned]);
  const isPinned = useCallback((name: string) => pinned.includes(name), [pinned]);
  const togglePin = useCallback((name: string) => {
    setPinned((current) => current.includes(name) ? current.filter((n) => n !== name) : [...current, name]);
  }, []);
  return { isPinned, togglePin };
}
