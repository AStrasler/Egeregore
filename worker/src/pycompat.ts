/**
 * Small helpers that make JavaScript string handling behave like the Python
 * code this Worker is a port of (scripts/coordination.py and mcp/egregore_mcp).
 * Parity is the point: the same comment must classify the same way in both.
 */

/** The characters Python's str.isspace() accepts. JavaScript's \s differs (\x1c-\x1f, \ufeff). */
export const PY_WS =
  "\\t\\n\\v\\f\\r \\x1c-\\x1f\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";

/** Python's \b before a word character, as a lookbehind. Needs the u flag. */
export const WORD_START = "(?<![\\p{L}\\p{N}_])";
/** Python's \b after a word character, as a lookahead. Needs the u flag. */
export const WORD_END = "(?![\\p{L}\\p{N}_])";

const STRIP_RE = new RegExp(`^[${PY_WS}]+|[${PY_WS}]+$`, "gu");
const LINE_BREAK_RE = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/u;

/** str.strip() */
export function pyStrip(value: string): string {
  return value.replace(STRIP_RE, "");
}

/** str.splitlines(): splits on every Python line boundary and drops one trailing empty piece. */
export function pySplitLines(value: string): string[] {
  const parts = value.split(LINE_BREAK_RE);
  if (parts.length > 0 && parts[parts.length - 1] === "") {
    parts.pop();
  }
  return parts;
}

/** body.replace("\r\n", "\n").split("\n") */
export function splitNewlines(value: string): string[] {
  return value.replaceAll("\r\n", "\n").split("\n");
}

/** str(value or ""): the empty string for any falsy value. */
export function textOrEmpty(value: unknown): string {
  return value ? String(value) : "";
}

/** str(value) for an id that may be missing. Python prints None for null. */
export function pyStr(value: unknown): string {
  if (value === null || value === undefined) {
    return "None";
  }
  if (typeof value === "boolean") {
    return value ? "True" : "False";
  }
  return String(value);
}

const ISO_RE = new RegExp(
  "^(\\d{4})-(\\d{2})-(\\d{2})[T ](\\d{2}):(\\d{2})(?::(\\d{2})(?:[.,](\\d{1,6}))?)?(Z|[+-]\\d{2}(?::?\\d{2})?)$",
);

/**
 * datetime.fromisoformat for the shapes GitHub and the lease lines use, as epoch
 * milliseconds. A value with no time zone is rejected, as coordination.parse_time does.
 */
export function tryParseTime(value: string): number | null {
  const match = ISO_RE.exec(value);
  if (!match) {
    return null;
  }
  const [, y, mo, d, h, mi, s = "0", frac = "0", zone] = match;
  const year = Number(y);
  const month = Number(mo);
  const day = Number(d);
  const hour = Number(h);
  const minute = Number(mi);
  const second = Number(s);
  if (month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59) {
    return null;
  }
  const probe = new Date(0);
  probe.setUTCFullYear(year, month - 1, day);
  if (probe.getUTCFullYear() !== year || probe.getUTCMonth() !== month - 1 || probe.getUTCDate() !== day) {
    return null;
  }
  let offsetMinutes = 0;
  if (zone !== "Z") {
    const sign = zone.startsWith("-") ? -1 : 1;
    const digits = zone.slice(1).replace(":", "");
    const oh = Number(digits.slice(0, 2));
    const om = digits.length > 2 ? Number(digits.slice(2, 4)) : 0;
    if (oh > 23 || om > 59) {
      return null;
    }
    offsetMinutes = sign * (oh * 60 + om);
  }
  const millis = Number(frac.padEnd(3, "0").slice(0, 3));
  const instant = new Date(0);
  instant.setUTCFullYear(year, month - 1, day);
  instant.setUTCHours(hour, minute, second, millis);
  return instant.getTime() - offsetMinutes * 60_000;
}

/** strftime("%Y-%m-%dT%H:%M:%SZ") on a UTC instant. */
export function stamp(epochMs: number): string {
  return new Date(epochMs).toISOString().slice(0, 19) + "Z";
}

/** value[:count] counted in code points, as Python slices a str. */
export function pyHead(value: string, count: number): string {
  const points = Array.from(value);
  return points.length <= count ? value : points.slice(0, count).join("");
}

/** Python truthiness: empty strings, lists and dicts are false, as are 0, null and false. */
export function pyTruthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === 0 || value === "") {
    return false;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (typeof value === "object") {
    return Object.keys(value as object).length > 0;
  }
  return true;
}

/** urllib.parse.quote(value, safe=""): encodeURIComponent also leaves ! ' ( ) * alone. */
export function pyQuote(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
}

/** len(value): code points, not UTF-16 units. */
export function pyLen(value: string): number {
  let count = 0;
  for (const _ of value) {
    count += 1;
  }
  return count;
}
