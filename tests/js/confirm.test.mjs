import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const confirmSource = readFileSync(
  new URL("../../src/privasheet/web/static/app/confirm.js", import.meta.url),
  "utf8",
);
const reactSetupSource = readFileSync(
  new URL("../../src/privasheet/web/static/app/react-setup.js", import.meta.url),
  "utf8",
);

assert.match(confirmSource, /Promise<boolean>/);
assert.match(confirmSource, /bootstrap\.Modal/);
assert.match(confirmSource, /data-delete-action/);
assert.match(reactSetupSource, /htm\.bind\(React\.createElement\)/);
