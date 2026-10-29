import { toHex, sha256, PERMISSIONS, type Permission } from "../../src/auth";

/** A fresh random token each run. Nothing token-shaped is ever written to a file. */
export function randomToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return `tok_${btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}`;
}

export interface RecordOptions {
  agent?: string;
  permissions?: readonly Permission[];
  repos?: readonly string[];
  tools?: readonly string[];
  label?: string;
}

/** One config record for `token`, with every permission on the test repository unless told otherwise. */
export async function recordFor(token: string, options: RecordOptions = {}): Promise<Record<string, unknown>> {
  return {
    sha256: toHex(await sha256(token)),
    agent: options.agent ?? "cursor",
    permissions: options.permissions ?? PERMISSIONS,
    repos: options.repos ?? ["AStrasler/Egeregore"],
    tools: options.tools ?? ["*"],
    ...(options.label === undefined ? {} : { label: options.label }),
  };
}

export const bearer = (token: string): Record<string, string> => ({ authorization: `Bearer ${token}` });
