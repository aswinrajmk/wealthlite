// Creates .dev.vars (gitignored) with a random local-only SESSION_SECRET if it does not exist yet.
import { randomBytes } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";

const current = existsSync(".dev.vars") ? readFileSync(".dev.vars", "utf8") : "";
if (!/^SESSION_SECRET=/m.test(current)) {
  writeFileSync(".dev.vars", `${current}SESSION_SECRET=${randomBytes(32).toString("hex")}\n`);
  console.log("Created a local SESSION_SECRET in .dev.vars");
}
