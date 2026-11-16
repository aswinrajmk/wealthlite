// Password hashing (PBKDF2) and signed session cookies, using Web Crypto only.

const encoder = new TextEncoder();
const SESSION_COOKIE = "wl_session";
const SESSION_TTL_SECONDS = 60 * 60 * 8;

const toHex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const fromHex = (hex: string) => new Uint8Array(hex.match(/../g)?.map((h) => parseInt(h, 16)) ?? []);

// Stored format: pbkdf2$<iterations>$<salt hex>$<hash hex>
export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [scheme, iterations, saltHex, hashHex] = stored.split("$");
  if (scheme !== "pbkdf2" || !iterations || !saltHex || !hashHex) return false;
  const key = await crypto.subtle.importKey("raw", encoder.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: fromHex(saltHex), iterations: Number(iterations) },
    key,
    256,
  );
  const expected = fromHex(hashHex);
  return bits.byteLength === expected.byteLength && crypto.subtle.timingSafeEqual(bits, expected);
}

async function hmac(secret: string, data: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
  ]);
  return toHex(await crypto.subtle.sign("HMAC", key, encoder.encode(data)));
}

export async function createSessionCookie(userId: string, secret: string): Promise<string> {
  const expires = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const payload = `${userId}.${expires}`;
  const value = `${payload}.${await hmac(secret, payload)}`;
  return `${SESSION_COOKIE}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_TTL_SECONDS}`;
}

export const clearSessionCookie = () => `${SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;

export async function readSession(request: Request, secret: string): Promise<string | null> {
  const cookie = request.headers.get("Cookie") ?? "";
  const value = cookie
    .split(/;\s*/)
    .find((c) => c.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1);
  if (!value) return null;
  const [userId, expires, signature] = value.split(".");
  if (!userId || !expires || !signature) return null;
  if (Number(expires) < Date.now() / 1000) return null;
  const expected = await hmac(secret, `${userId}.${expires}`);
  const a = encoder.encode(signature);
  const b = encoder.encode(expected);
  return a.byteLength === b.byteLength && crypto.subtle.timingSafeEqual(a, b) ? userId : null;
}
