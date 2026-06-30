import { act, renderHook } from "@testing-library/react";
import { Classes } from "@blueprintjs/core";
import { useTheme } from "./useTheme";

describe("useTheme", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        clear: () => values.clear(),
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
      },
    });
    document.body.className = "";
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ matches: false }),
    });
  });

  it("persists a theme override and applies Blueprint dark mode", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
    act(() => result.current.toggleTheme());
    expect(document.body).toHaveClass(Classes.DARK);
    expect(window.localStorage.getItem("pfui-theme")).toBe("dark");
  });
});
