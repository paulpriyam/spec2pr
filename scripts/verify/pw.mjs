// Playwright helper for the app-verifier agent (web pages and Chrome extensions).
// Playwright itself lives in ~/.feature-factory/verify (set up by scripts/verify-env.sh setup web).
//
// Quick look (no script needed):
//   node pw.mjs look <url> <evidenceDir> <name> [--extension <dir>] [--mobile]
//     -> saves <name>.png + <name>.aria.txt (accessibility tree) and prints the tree
//
// In a script:
//   import { start, shot, finish } from "<plugin>/scripts/verify/pw.mjs";
//   const s = await start({ evidence: "/abs/dir", extension: "/abs/ext-dir" });  // extension optional
//   await s.page.goto("http://localhost:3000"); await shot(s, "AC-1-home");
//   // extension pages: await s.page.goto(`chrome-extension://${s.extensionId}/popup.html`)
//   await finish(s);   // writes console-errors.txt if the page logged errors
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import { join, resolve } from "node:path";

const VDIR = process.env.FF_VERIFY_DIR || join(homedir(), ".feature-factory", "verify");
let playwright;
try {
  playwright = createRequire(join(VDIR, "package.json"))("playwright");
} catch {
  console.error(`Playwright is not set up. Run: <plugin>/scripts/verify-env.sh setup web`);
  process.exit(3);
}
const { chromium, devices } = playwright;

export async function start({ evidence, extension, headless = true, mobile = false, viewport } = {}) {
  if (!evidence) throw new Error("start(): evidence dir is required");
  mkdirSync(evidence, { recursive: true });
  const ctxOpts = mobile ? { ...devices["Pixel 7"] } : { viewport: viewport || { width: 1280, height: 860 } };
  const s = { evidence, consoleErrors: [], extensionId: null };
  if (extension) {
    const ext = resolve(extension);
    s.context = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), "ff-ext-")), {
      channel: "chromium", headless, ...ctxOpts,
      args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`],
    });
    let [sw] = s.context.serviceWorkers();
    if (!sw && !s.context.backgroundPages().length) {
      sw = await s.context.waitForEvent("serviceworker", { timeout: 10000 }).catch(() => null);
    }
    const bg = sw || s.context.backgroundPages()[0];
    s.extensionId = bg ? new URL(bg.url()).host : null;
  } else {
    s.browser = await chromium.launch({ headless });
    s.context = await s.browser.newContext(ctxOpts);
  }
  s.context.on("page", (p) => watch(s, p));
  s.page = s.context.pages()[0] || (await s.context.newPage());
  watch(s, s.page);
  return s;
}

const watched = new WeakSet();
function watch(s, page) {
  if (watched.has(page)) return;
  watched.add(page);
  page.on("console", (m) => m.type() === "error" && s.consoleErrors.push(`[console] ${m.text()}`));
  page.on("pageerror", (e) => s.consoleErrors.push(`[pageerror] ${e.message}`));
}

// Screenshot + accessibility tree of the current page (or `page`), named for the AC it proves.
export async function shot(s, name, page = s.page, { fullPage = true } = {}) {
  const png = join(s.evidence, `${name}.png`);
  await page.screenshot({ path: png, fullPage });
  let aria = "";
  try { aria = await page.locator("body").ariaSnapshot(); } catch (e) { aria = `(no accessibility tree: ${e.message})`; }
  writeFileSync(join(s.evidence, `${name}.aria.txt`), aria);
  return { png, aria };
}

export async function finish(s) {
  if (s.consoleErrors.length) writeFileSync(join(s.evidence, "console-errors.txt"), s.consoleErrors.join("\n") + "\n");
  await s.context?.close();
  await s.browser?.close();
  return s.consoleErrors;
}

// CLI: node pw.mjs look <url> <evidenceDir> <name> [--extension <dir>] [--mobile]
if (import.meta.url === `file://${process.argv[1]}`) {
  const [cmd, url, evidence, name, ...rest] = process.argv.slice(2);
  if (cmd !== "look" || !url || !evidence || !name) {
    console.error("usage: node pw.mjs look <url> <evidenceDir> <name> [--extension <dir>] [--mobile]");
    process.exit(2);
  }
  const ei = rest.indexOf("--extension");
  const s = await start({ evidence, extension: ei >= 0 ? rest[ei + 1] : undefined, mobile: rest.includes("--mobile") });
  const target = s.extensionId ? url.replace("{ext}", s.extensionId) : url;
  try {
    await s.page.goto(target, { waitUntil: "networkidle", timeout: 30000 }).catch(() => s.page.waitForLoadState("load"));
    const { png, aria } = await shot(s, name);
    console.log(`screenshot: ${png}${s.extensionId ? `\nextension id: ${s.extensionId}` : ""}\n--- accessibility tree ---\n${aria}`);
  } finally {
    const errs = await finish(s);
    if (errs.length) console.log(`--- page errors ---\n${errs.join("\n")}`);
  }
}
