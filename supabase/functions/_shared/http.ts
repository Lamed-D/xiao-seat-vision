// Small HTTP helpers shared by all xiao-seat-vision Edge Functions.

export const MAX_JSON_BYTES = 16 * 1024;

export function json(
  status: number,
  body: unknown,
  headers: HeadersInit = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...headers,
    },
  });
}

export type BodyResult =
  | { ok: true; value: unknown }
  | { ok: false; response: Response };

/** Reads and parses a JSON body with content-type and size checks. */
export async function readJson(
  req: Request,
  maxBytes = MAX_JSON_BYTES,
): Promise<BodyResult> {
  const contentType = req.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return {
      ok: false,
      response: json(415, { error: "unsupported_media_type" }),
    };
  }
  const declared = Number(req.headers.get("content-length") ?? "0");
  if (declared > maxBytes) {
    return { ok: false, response: json(413, { error: "payload_too_large" }) };
  }
  const text = await req.text();
  if (new TextEncoder().encode(text).length > maxBytes) {
    return { ok: false, response: json(413, { error: "payload_too_large" }) };
  }
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false, response: json(400, { error: "invalid_json" }) };
  }
}

/** Error thrown by stores when the DB rejects data (CHECK / NOT NULL / type). */
export class ConstraintError extends Error {}

/** Postgres error codes that mean "bad data", not "server down". */
export const CONSTRAINT_CODES = new Set(["23514", "23502", "22P02", "22003"]);

/** Logs without secrets: only the message and selected safe fields. */
export function logError(context: string, err: unknown): void {
  const message = err instanceof Error ? err.message : String(err);
  console.error(`[${context}] ${message}`);
}
