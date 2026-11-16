import { useEffect, useState } from "react";
import { session, type User } from "./api";
import { LoginPage } from "./LoginPage";
import { PortfolioPage } from "./PortfolioPage";

type View = { name: "loading" } | { name: "login" } | { name: "portfolio"; user: User };

function go(path: string) {
  if (window.location.pathname !== path) window.history.replaceState(null, "", path);
}

export function App() {
  const [view, setView] = useState<View>({ name: "loading" });

  useEffect(() => {
    void session().then((result) => {
      if (result.ok && result.data.user) {
        go("/portfolio");
        setView({ name: "portfolio", user: result.data.user });
      } else {
        go("/login");
        setView({ name: "login" });
      }
    });
  }, []);

  return (
    <>
      <div className="demo-banner" role="note">
        Demo app with synthetic data. It exists so automated tests have something real to test.
      </div>
      {view.name === "loading" && <p className="loading">Loading…</p>}
      {view.name === "login" && (
        <LoginPage
          onSignedIn={(user) => {
            go("/portfolio");
            setView({ name: "portfolio", user });
          }}
        />
      )}
      {view.name === "portfolio" && (
        <PortfolioPage
          user={view.user}
          onSignedOut={() => {
            go("/login");
            setView({ name: "login" });
          }}
        />
      )}
    </>
  );
}
