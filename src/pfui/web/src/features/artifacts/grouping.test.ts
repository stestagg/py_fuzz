import { buildArtifactGroups, validateGroupSpec } from "./grouping";
import type { ArtifactSummary } from "../../protocol/types";

const artifact = (hash: string, type: "crash" | "core", values: string[]): ArtifactSummary => ({
  hash,
  type,
  path: `/artifacts/${hash}`,
  hasInput: false,
  inputSize: null,
  groupValues: values.map((value) => ({ value, label: value })),
});

describe("artifact grouping", () => {
  it("builds ordered nested groups", () => {
    const groups = buildArtifactGroups([
      artifact("one", "core", ["core", "worker-a"]),
      artifact("two", "core", ["core", "worker-b"]),
      artifact("three", "crash", ["crash", "worker-a"]),
    ], ["type", "meta:worker"]);
    expect(groups.map((group) => [group.label, group.count])).toEqual([["core", 2], ["crash", 1]]);
    expect(groups[0].groups.map((group) => group.label)).toEqual(["worker-a", "worker-b"]);
  });

  it("validates supported specifications", () => {
    expect(validateGroupSpec("type")).toBeNull();
    expect(validateGroupSpec("meta:worker")).toBeNull();
    expect(validateGroupSpec("file:../secret")).toMatch(/local filename/);
    expect(validateGroupSpec("unknown:value")).toMatch(/Use type/);
  });
});
