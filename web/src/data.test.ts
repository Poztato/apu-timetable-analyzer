import { describe, expect, it } from "vitest";

import { decodeTable } from "./data";

describe("decodeTable", () => {
  it("decodes columns and rows into records", () => {
    const decoded = decodeTable<{ code: string; score: number }>(
      {
        columns: ["code", "score"],
        rows: [
          ["A", 10],
          ["B", 20],
        ],
      },
      "Example",
    );

    expect(decoded).toEqual([
      { code: "A", score: 10 },
      { code: "B", score: 20 },
    ]);
  });

  it("rejects a row with the wrong width", () => {
    expect(() =>
      decodeTable(
        { columns: ["code", "score"], rows: [["A"]] },
        "Example",
      ),
    ).toThrow("wrong width");
  });
});
