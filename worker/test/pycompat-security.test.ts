import { describe, expect, it } from "vitest";
import { pyStrip } from "../src/pycompat";

describe("Python-compatible bounded whitespace stripping", () => {
  it("strips every Python whitespace code point and preserves BOM", () => {
    const codes = [9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760,
      ...Array.from({ length: 11 }, (_, i) => 8192 + i), 8232, 8233, 8239, 8287, 12288];
    for (const code of codes) {
      const char = String.fromCodePoint(code);
      expect(pyStrip(`${char}??text??${char}`)).toBe("??text??");
      expect(pyStrip(char.repeat(3))).toBe("");
    }
    expect(pyStrip(" \ufefftext\ufeff ")).toBe("\ufefftext\ufeff");
    expect(pyStrip("")).toBe("");
  });

  it("preserves large internal whitespace runs followed by nonspace", () => {
    for (const whitespace of [" ", "\u2003", "\x1c"]) {
      const internal = `x${whitespace.repeat(200_000)}x`;
      expect(pyStrip(internal)).toBe(internal);
      expect(pyStrip(`\t${internal}\n`)).toBe(internal);
    }
  });
});

