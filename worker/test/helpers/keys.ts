/** Test-only RSA keys. Generated at run time, so no key is ever committed. */

function toBase64(bytes: Uint8Array): string {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text);
}

export function pem(label: string, der: Uint8Array): string {
  const body = toBase64(der).replace(/(.{64})/g, "$1\n").replace(/\n$/, "");
  return `-----BEGIN ${label}-----\n${body}\n-----END ${label}-----\n`;
}

/** Reads one DER element at `offset`. Returns the content slice and the next offset. */
function element(der: Uint8Array, offset: number): { tag: number; content: Uint8Array; next: number } {
  const tag = der[offset];
  let length = der[offset + 1];
  let start = offset + 2;
  if (length & 0x80) {
    const count = length & 0x7f;
    length = 0;
    for (let index = 0; index < count; index += 1) {
      length = length * 256 + der[start + index];
    }
    start += count;
  }
  return { tag, content: der.subarray(start, start + length), next: start + length };
}

/** Pulls the PKCS#1 RSAPrivateKey out of a PKCS#8 PrivateKeyInfo. */
export function pkcs8ToPkcs1(pkcs8: Uint8Array): Uint8Array {
  const outer = element(pkcs8, 0).content;
  const version = element(outer, 0);
  const algorithm = element(outer, version.next);
  const octets = element(outer, algorithm.next);
  if (octets.tag !== 0x04) throw new Error("unexpected PKCS#8 layout");
  return octets.content;
}

export interface TestKey {
  pkcs8Pem: string;
  pkcs1Pem: string;
  publicKey: CryptoKey;
}

let cached: Promise<TestKey> | null = null;

export function testKey(): Promise<TestKey> {
  cached ??= (async () => {
    const pair = (await crypto.subtle.generateKey(
      {
        name: "RSASSA-PKCS1-v1_5",
        modulusLength: 2048,
        publicExponent: new Uint8Array([1, 0, 1]),
        hash: "SHA-256",
      },
      true,
      ["sign", "verify"],
    )) as CryptoKeyPair;
    const pkcs8 = new Uint8Array((await crypto.subtle.exportKey("pkcs8", pair.privateKey)) as ArrayBuffer);
    return {
      pkcs8Pem: pem("PRIVATE KEY", pkcs8),
      pkcs1Pem: pem("RSA PRIVATE KEY", pkcs8ToPkcs1(pkcs8)),
      publicKey: pair.publicKey,
    };
  })();
  return cached;
}

function fromBase64Url(text: string): Uint8Array {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (ch) => ch.charCodeAt(0));
}

/** Checks an RS256 JWT against the public key. Returns the decoded header and claims. */
export async function verifyJwt(
  jwt: string,
  publicKey: CryptoKey,
): Promise<{ header: Record<string, unknown>; claims: Record<string, unknown> }> {
  const [head, claims, signature] = jwt.split(".");
  const valid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    publicKey,
    fromBase64Url(signature),
    new TextEncoder().encode(`${head}.${claims}`),
  );
  if (!valid) throw new Error("JWT signature did not verify");
  const decode = (part: string) => JSON.parse(new TextDecoder().decode(fromBase64Url(part)));
  return { header: decode(head), claims: decode(claims) };
}
