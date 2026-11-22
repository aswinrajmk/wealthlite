import { useEffect, useMemo, useState } from "react";
import { formatGbp, holdings, logout, type HoldingsResponse, type User } from "./api";

type SortOrder = "none" | "ascending" | "descending";

export function PortfolioPage({ user, onSignedOut }: { user: User; onSignedOut: () => void }) {
  const [data, setData] = useState<HoldingsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [valueSort, setValueSort] = useState<SortOrder>("none");

  const rows = useMemo(() => {
    if (!data) return [];
    if (valueSort === "none") return data.holdings;
    const dir = valueSort === "ascending" ? 1 : -1;
    return [...data.holdings].sort((a, b) => dir * (a.valuePence - b.valuePence));
  }, [data, valueSort]);

  useEffect(() => {
    void holdings().then((result) => (result.ok ? setData(result.data) : setError(result.message)));
  }, []);

  async function signOut() {
    await logout();
    onSignedOut();
  }

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">WealthLite</span>
        <span className="topbar__user">{user.email}</span>
        <button type="button" className="secondary" onClick={signOut} data-testid="sign-out">
          Sign out
        </button>
      </header>
      <main className="portfolio">
        <h1 data-testid="greeting">Welcome back, {user.name}</h1>
        {error && (
          <p role="alert" className="login__error">
            {error}
          </p>
        )}
        {data && (
          <>
            <section className="total" aria-labelledby="total-label">
              <p id="total-label">Total portfolio value</p>
              <p className="total__value" data-testid="total-value">
                {formatGbp(data.totalPence)}
              </p>
            </section>
            <div className="table-wrap">
              <table data-testid="holdings-table">
                <caption>Your holdings</caption>
                <thead>
                  <tr>
                    <th scope="col">Fund</th>
                    <th scope="col">Asset class</th>
                    <th scope="col">Units</th>
                    <th scope="col">Unit price</th>
                    <th scope="col" aria-sort={valueSort}>
                      <button
                        type="button"
                        className="sort"
                        data-testid="sort-value"
                        data-sort={valueSort}
                        onClick={() => setValueSort(valueSort === "ascending" ? "descending" : "ascending")}
                      >
                        Value
                        <span className="sort__arrows" aria-hidden="true">
                          <span className={valueSort === "ascending" ? "sort__arrow--on" : undefined}>▲</span>
                          <span className={valueSort === "descending" ? "sort__arrow--on" : undefined}>▼</span>
                        </span>
                        <span className="visually-hidden">
                          {valueSort === "ascending"
                            ? ", sorted low to high"
                            : valueSort === "descending"
                              ? ", sorted high to low"
                              : ", not sorted"}
                        </span>
                      </button>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((h) => (
                    <tr key={h.id} data-testid="holding-row">
                      <th scope="row">
                        {h.fundName} <span className="ticker">{h.ticker}</span>
                      </th>
                      <td>{h.assetClass}</td>
                      <td className="num">{h.units.toLocaleString("en-GB")}</td>
                      <td className="num">{formatGbp(h.unitPricePence)}</td>
                      <td className="num" data-testid="holding-value">
                        {formatGbp(h.valuePence)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        {!data && !error && <p className="loading">Loading holdings…</p>}
      </main>
    </div>
  );
}
