// Token authentication shared by the Edge Functions.
//
// Tokens come from comma-separated secrets (e.g. BRIDGE_API_TOKENS="tokA,tokB")
// so they can be rotated without downtime. Tokens shorter than 32 chars are ignored.

export const MIN_TOKEN_LENGTH = 32;

export function parseTokens(raw: string | undefined | null): string[] {
  return (raw ?? "")
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t.length >= MIN_TOKEN_LENGTH);
}

async function sha256(text: string): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return new Uint8Array(digest);
}

/** Constant-time comparison (hash both sides, then compare fixed-length digests). */
export async function tokenMatches(
  presented: string | null,
  allowed: readonly string[],
): Promise<boolean> {
  if (!presented || allowed.length === 0) return false;
  const p = await sha256(presented);
  let match = false;
  for (const token of allowed) {
    const t = await sha256(token);
    let diff = 0;
    for (let i = 0; i < p.length; i++) diff |= p[i] ^ t[i];
    if (diff === 0) match = true;
  }
  return match;
}
