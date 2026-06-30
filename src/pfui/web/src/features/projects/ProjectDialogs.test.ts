import { describe, expect, it } from "vitest";
import { projectNameError } from "./ProjectDialogs";

describe("projectNameError", () => {
  it("accepts safe new project names", () => {
    expect(projectNameError("cpython-main_3.14", ["existing"])).toBeNull();
  });

  it("rejects traversal, spaces, and duplicates", () => {
    expect(projectNameError("../escape", [])).not.toBeNull();
    expect(projectNameError("two words", [])).not.toBeNull();
    expect(projectNameError("existing", ["existing"])).not.toBeNull();
  });
});
