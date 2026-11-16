# WealthLite

A small demo wealth-management app with **synthetic data only**. It exists to be tested: it is the app
under test for [qa-tests](https://github.com/aswinrajmk/qa-tests), and the results appear live on the
[QA dashboard](https://portfolio.aswin-qa.workers.dev/dashboard/).

Live: https://wealthlite.aswin-qa.workers.dev (not indexed by search engines).

- **Login:** `demo@wealthlite.test` / `demo-pass-2026`. Synthetic, and shown on the login page.
- **Portfolio:** greeting, total value and a holdings table of made-up funds.
- **Stack:** Vite + React + TypeScript, served by a Cloudflare Worker with D1. Passwords are stored as
  PBKDF2 hashes, and sessions are HMAC-signed HttpOnly cookies.

## API

| Route               | Auth    | Returns                                         |
| ------------------- | ------- | ----------------------------------------------- |
| `GET /api/health`   | none    | `{ ok: true }`                                  |
| `GET /api/session`  | none    | `{ user }`, or `{ user: null }` when signed out |
| `POST /api/login`   | none    | user + session cookie, or 401                   |
| `POST /api/logout`  | none    | clears the cookie                               |
| `GET /api/holdings` | session | holdings, `totalPence`, `currency`              |

## Run the demo: break something and watch the tests catch it

1. Open [`src/LoginPage.tsx`](src/LoginPage.tsx) and change `SIGN_IN_LABEL` from `"Sign in"` to
   `"Log in"`.
2. Commit and push to `main`.
3. The **Deploy and trigger tests** workflow deploys the app, then triggers qa-tests.
4. Open the [QA dashboard](https://portfolio.aswin-qa.workers.dev/dashboard/). The run appears within
   about a minute and updates live:
   - **Flow tests** (sign in, wrong password) show as **Healed**. The button is found by its
     `data-testid`, and the report suggests the fixed locator.
   - **The copy test** fails, with the expected and actual text and a screenshot.
5. Change the label back and push again to return to green.

`data-testid` attributes on key elements are deliberate. A text change can be healed; removing a test
id cannot.

## Run locally

```sh
npm install
npm run dev        # creates .dev.vars with a local SESSION_SECRET and migrates local D1
npm run lint && npm run typecheck && npm run build
```

## Secrets

| Where              | Name                                            | Purpose                                                       |
| ------------------ | ----------------------------------------------- | ------------------------------------------------------------- |
| GitHub (this repo) | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | Deploy (same values as the portfolio repo)                    |
| GitHub (this repo) | `QA_DISPATCH_TOKEN`                             | Fine-grained token allowed to trigger workflows in `qa-tests` |
| Cloudflare Worker  | `SESSION_SECRET`                                | Signs session cookies. Set once with `wrangler secret put`    |
