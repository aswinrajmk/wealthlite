import { clearSessionCookie, createSessionCookie, readSession, verifyPassword } from "./auth";

// WealthLite: a small demo wealth app with synthetic data. It exists to be tested.

interface UserRow {
  id: string;
  email: string;
  display_name: string;
  password_hash: string;
}

interface HoldingRow {
  id: string;
  fund_name: string;
  ticker: string;
  asset_class: string;
  units: number;
  unit_price_pence: number;
}

const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  Response.json(body, { status, headers: { "Cache-Control": "no-store", ...headers } });

async function api(request: Request, env: Env, path: string): Promise<Response> {
  if (path === "/api/health" && request.method === "GET") return json({ ok: true });

  if (path === "/api/login" && request.method === "POST") {
    const body = (await request.json().catch(() => null)) as { email?: unknown; password?: unknown } | null;
    const email = typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";
    const password = typeof body?.password === "string" ? body.password : "";
    if (!email || !password) return json({ error: "missing_fields", message: "Enter your email and password." }, 400);

    const user = await env.DB.prepare("SELECT * FROM users WHERE email = ?1").bind(email).first<UserRow>();
    if (!user || !(await verifyPassword(password, user.password_hash))) {
      return json({ error: "invalid_credentials", message: "Email or password is incorrect." }, 401);
    }
    return json({ name: user.display_name, email: user.email }, 200, {
      "Set-Cookie": await createSessionCookie(user.id, env.SESSION_SECRET),
    });
  }

  if (path === "/api/logout" && request.method === "POST") {
    return json({ ok: true }, 200, { "Set-Cookie": clearSessionCookie() });
  }

  const userId = await readSession(request, env.SESSION_SECRET);

  // Always 200 so a signed-out page load is not an error; `user` is null when there is no valid session.
  if (path === "/api/session" && request.method === "GET") {
    const user = userId
      ? await env.DB.prepare("SELECT * FROM users WHERE id = ?1").bind(userId).first<UserRow>()
      : null;
    return json({ user: user ? { name: user.display_name, email: user.email } : null });
  }

  if (!userId) return json({ error: "unauthorized", message: "Sign in to continue." }, 401);

  if (path === "/api/holdings" && request.method === "GET") {
    const { results } = await env.DB.prepare("SELECT * FROM holdings WHERE user_id = ?1 ORDER BY fund_name")
      .bind(userId)
      .all<HoldingRow>();
    const holdings = results.map((h) => ({
      id: h.id,
      fundName: h.fund_name,
      ticker: h.ticker,
      assetClass: h.asset_class,
      units: h.units,
      unitPricePence: h.unit_price_pence,
      valuePence: Math.round(h.units * h.unit_price_pence),
    }));
    const totalPence = holdings.reduce((sum, h) => sum + h.valuePence, 0);
    return json({ holdings, totalPence, currency: "GBP" });
  }

  return json({ error: "not_found" }, 404);
}

export default {
  async fetch(request, env): Promise<Response> {
    const path = new URL(request.url).pathname;
    const response = path.startsWith("/api/") ? await api(request, env, path) : await env.ASSETS.fetch(request);
    const headers = new Headers(response.headers);
    headers.set("X-Robots-Tag", "noindex");
    headers.set("X-Content-Type-Options", "nosniff");
    headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
    if (!import.meta.env.DEV) {
      headers.set(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
      );
    }
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  },
} satisfies ExportedHandler<Env>;
