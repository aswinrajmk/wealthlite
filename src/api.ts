export interface User {
  name: string;
  email: string;
}

export interface Holding {
  id: string;
  fundName: string;
  ticker: string;
  assetClass: string;
  units: number;
  unitPricePence: number;
  valuePence: number;
}

export interface HoldingsResponse {
  holdings: Holding[];
  totalPence: number;
  currency: string;
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<{ ok: true; data: T } | { ok: false; message: string }> {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const body = (await response.json().catch(() => ({}))) as T & { message?: string };
  return response.ok ? { ok: true, data: body } : { ok: false, message: body.message ?? "Something went wrong." };
}

export const login = (email: string, password: string) =>
  request<User>("/api/login", { method: "POST", body: JSON.stringify({ email, password }) });
export const logout = () => request<{ ok: true }>("/api/logout", { method: "POST" });
export const session = () => request<{ user: User | null }>("/api/session");
export const holdings = () => request<HoldingsResponse>("/api/holdings");

export const formatGbp = (pence: number) =>
  new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" }).format(pence / 100);
