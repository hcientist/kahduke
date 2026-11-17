// Accessibility scan of every KahDuke page, with axe-core in Chromium.
//
// It crawls the site twice, signed out and signed in, following every
// same-origin link, and plays one whole game (host and player screens), since
// those screens are drawn by JavaScript and have no links to follow. Then it
// checks that every Django route was scanned or is listed in exclude.json.
//
// Writes a11y-report.md and a11y-results.json to the current directory.
// Exit codes:
//   0  the scan ran completely (it may still have found violations; the
//      workflow reads the count from a11y-results.json)
//   1  the scan couldn't run at all
//   2  the scan ran but is incomplete: a page failed to load, a game step
//      broke, sign-in failed, or a route was neither scanned nor excluded.
//      This usually means the app changed and this script needs updating.
//      The "Scan problems" section of the report says what went wrong.
//
// Environment:
//   BASE_URL       the running site (default http://127.0.0.1:8000)
//   A11Y_APP_INFO  output of app_info.py for that site (default app.json)
//   A11Y_USER      an existing account to sign in with
//   A11Y_PASSWORD  its password
//   A11Y_TAGS      comma-separated axe tags (default WCAG 2.2 A and AA)
//   A11Y_MAX_PAGES pages per crawl (default 200)

import { readFile, writeFile } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { chromium } from "playwright";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const ORIGIN = new URL(BASE).origin;
const USER = process.env.A11Y_USER;
const PASSWORD = process.env.A11Y_PASSWORD;
const TAGS = (process.env.A11Y_TAGS ||
  "wcag2a,wcag2aa,wcag21a,wcag21aa,wcag22a,wcag22aa").split(",").map((t) => t.trim());
const MAX_PAGES = Number(process.env.A11Y_MAX_PAGES || 200);
const APP_INFO = process.env.A11Y_APP_INFO || "app.json";

let app;
try {
  app = JSON.parse(await readFile(APP_INFO, "utf8"));
} catch (error) {
  console.error(`Can't read ${APP_INFO} (${error.message}).`);
  console.error("Make it with: uv run python a11y/app_info.py > a11y/app.json");
  process.exit(1);
}
const ROUTES = app.routes.map((r) => ({ ...r, re: new RegExp(r.regex) }));

// Routes the scan doesn't cover, and why, from exclude.json (next to this
// script). Each entry has:
//   route  the Django route as app_info.py prints it; a trailing "*" matches
//          every route under it
//   why    the reason, shown in the report
//   skip   optional; true also stops the crawler from following links to
//          those routes. Use it for anything that changes data on GET, signs
//          out, leaves the site, or isn't an HTML page.
// A route that's neither scanned nor listed there fails the scan, and so does
// an entry there that no longer matches any route.
const EXCLUDE_FILE = new URL("exclude.json", import.meta.url);
const EXCLUDE = JSON.parse(await readFile(EXCLUDE_FILE, "utf8"));

// Pages that answer with an error status on purpose.
const ERROR_STATUS = {
  "accounts/3rdparty/login/error/": 401,
  "accounts/social/login/error/": 401,
};

// Not Django routes, and never pages.
const NOT_PAGES = [/^\/static\//, /^\/media\//];

const scans = []; // { context, url, violations }
const problems = []; // anything that made the scan incomplete
const covered = new Set(); // Django routes scanned, by route string

function routeOf(href) {
  const path = new URL(href, BASE).pathname;
  return ROUTES.find((r) => r.re.test(path));
}

function excludedBy(route) {
  return EXCLUDE.find((e) =>
    e.route.endsWith("*") ? route.route.startsWith(e.route.slice(0, -1)) : route.route === e.route);
}

function cover(href) {
  const route = routeOf(href);
  if (route) covered.add(route.route);
}

async function scan(page, context) {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const url = page.url().replace(ORIGIN, "");
  scans.push({ context, url, violations: results.violations });
  cover(page.url());
  const n = results.violations.reduce((sum, v) => sum + v.nodes.length, 0);
  console.log(`${n ? "✗" : "✓"} [${context}] ${url}${n ? ` (${n})` : ""}`);
}

function followable(href) {
  let url;
  try {
    url = new URL(href, BASE);
  } catch {
    return null;
  }
  if (url.origin !== ORIGIN) return null;
  if (NOT_PAGES.some((re) => re.test(url.pathname))) return null;
  const route = routeOf(url.href);
  if (route && excludedBy(route)?.skip) return null;
  url.hash = "";
  // "?next=" only changes where a form sends you afterwards, not the page.
  url.searchParams.delete("next");
  return url.href;
}

async function crawl(page, context, startPaths) {
  const queue = startPaths.map((p) => new URL(p, BASE).href);
  const seen = new Set(queue);
  const scanned = new Set();
  let count = 0;
  while (queue.length && count < MAX_PAGES) {
    const url = queue.shift();
    if (scanned.has(url)) continue;
    let response;
    try {
      response = await page.goto(url, { waitUntil: "networkidle" });
    } catch (error) {
      problems.push(`[${context}] ${url}: ${error.message.split("\n")[0]}`);
      continue;
    }
    const type = response?.headers()["content-type"] || "";
    if (!type.includes("text/html")) continue;
    const expected = ERROR_STATUS[routeOf(url)?.route];
    if (response.status() >= 400 && response.status() !== expected) {
      problems.push(`[${context}] ${url.replace(ORIGIN, "")}: HTTP ${response.status()}`);
    }
    // A redirect can land somewhere skipped, or somewhere already scanned.
    const landed = followable(page.url());
    if (!landed) continue;
    // A route that redirects to a scanned page counts as scanned, except a
    // redirect to sign in, which says nothing about the page itself.
    if (routeOf(landed)?.name !== "account_login") cover(url);
    if (scanned.has(landed)) continue;
    scanned.add(landed);
    count++;
    await scan(page, context);
    const hrefs = await page.$$eval("a[href]", (as) => as.map((a) => a.href));
    for (const href of hrefs) {
      const next = followable(href);
      if (next && !seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  if (queue.length) {
    problems.push(`[${context}] stopped at A11Y_MAX_PAGES=${MAX_PAGES}; ${queue.length} pages not scanned`);
  }
}

// The 404 page, which no link leads to.
async function notFound(page) {
  const response = await page.goto(`${BASE}/a11y-scan-no-such-page/`);
  if (response?.status() !== 404) {
    problems.push(`404 page: expected HTTP 404, got ${response?.status()}`);
  }
  await scan(page, "signed out: 404");
}

// Remix the demo kwiz without signing in, and return the private edit link
// that kwizzes made that way show, since nothing links to it.
async function signedOutSetup(page) {
  try {
    await openDemoKwiz(page);
    await page.getByTestId("remix").click();
    await page.waitForURL(/\/kwizzes\/\d+\/edit\/$/);
    await page.goto(page.url().replace(/edit\/$/, ""));
    return [await page.getByTestId("edit-link").inputValue()];
  } catch (error) {
    problems.push(`signed-out setup: ${error.message.split("\n")[0]}`);
    return [];
  }
}

async function openDemoKwiz(page) {
  if (!app.demo_kwiz) throw new Error(`no demo kwiz in ${APP_INFO}; run manage.py seed_demo first`);
  await page.goto(`${BASE}/kwizzes/${app.demo_kwiz}/`);
}

// Host a game in one browser, join it from two others, and play it to the
// end, scanning each new screen. The second player answers last, so the
// first player sees "answer locked in" before the kwestion closes. It finds controls by data-testid
// (in kwizzes/templates, games/templates and static/js/game.js), so changing
// their wording or styling doesn't break it.
async function playGame(browser) {
  const contexts = await Promise.all([1, 2, 3].map(() => browser.newContext()));
  const [host, player, player2] = await Promise.all(contexts.map((c) => c.newPage()));
  for (const page of [host, player, player2]) page.on("dialog", (d) => d.dismiss());
  const join = async (page, nickname, code) => {
    await page.goto(`${BASE}/play/?code=${code}`);
    await page.locator("input[name=nickname]").fill(nickname);
    await page.getByTestId("join").click();
    await page.waitForURL(/\/games\/[^/]+\/play\/$/);
    await page.getByTestId("player-lobby").waitFor();
  };
  const step = async (name, fn) => {
    try {
      await fn();
    } catch (error) {
      throw new Error(`game step "${name}": ${error.message.split("\n")[0]}`);
    }
  };

  try {
    await step("host", async () => {
      await openDemoKwiz(host);
      await host.getByTestId("host-game").click();
      await host.waitForURL(/\/games\/[^/]+\/host\/$/);
      await host.getByTestId("start-game").waitFor();
    });
    await scan(host, "game: host");
    const shortcode = host.url().match(/\/games\/([^/]+)\/host\//)[1];

    await step("join", async () => {
      await player.goto(`${BASE}/play/?code=${shortcode}`);
    });
    await scan(player, "game: player");
    await step("join", async () => {
      await join(player, "A11y Bot", shortcode);
      await join(player2, "A11y Bot 2", shortcode);
    });
    await scan(player, "game: player (lobby)");

    await step("start", async () => {
      const start = host.getByTestId("start-game");
      await start.and(host.locator(":enabled")).waitFor();
      await scan(host, "game: host (lobby with players)");
      await start.click();
    });

    const next = host.getByTestId("next-kwestion");
    const locked = player.getByTestId("answer-locked");
    let first = true;
    for (;;) {
      await step("kwestion", async () => {
        await player.getByTestId("answer-choice").first().waitFor();
        await player2.getByTestId("answer-choice").first().waitFor();
        await host.getByTestId("close-kwestion").waitFor();
      });
      if (first) {
        await scan(host, "game: host (kwestion)");
        await scan(player, "game: player (kwestion)");
      }
      await step("answer", async () => {
        await player.getByTestId("answer-choice").first().click();
        await locked.or(player.getByTestId("player-result")).waitFor();
      });
      if (first && await locked.isVisible()) await scan(player, "game: player (answered)");
      if (await player2.getByTestId("answer-choice").first().isVisible()) {
        await player2.getByTestId("answer-choice").last().click().catch(() => {});
      }

      await step("results", async () => {
        // Everyone answered, so results may already be showing.
        if (!(await next.isVisible())) {
          await host.getByTestId("close-kwestion").click({ timeout: 5000 }).catch(() => {});
        }
        await next.waitFor();
        await player.getByTestId("player-result").waitFor();
      });
      if (first) {
        await host.waitForTimeout(500);
        await scan(host, "game: host (results)");
        await scan(player, "game: player (results)");
      }
      first = false;
      const last = (await next.getAttribute("data-last")) !== null;
      await next.click();
      if (last) break;
    }

    await step("final", async () => {
      await host.getByTestId("final-results").waitFor();
      await player.getByTestId("final-results").waitFor();
    });
    await scan(host, "game: host (final)");
    await scan(player, "game: player (final)");
  } catch (error) {
    problems.push(error.message);
  } finally {
    await Promise.all(contexts.map((c) => c.close()));
  }
}

async function signedIn(browser) {
  if (!USER || !PASSWORD) {
    problems.push("A11Y_USER/A11Y_PASSWORD not set; signed-in pages not scanned");
    return;
  }
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("dialog", (d) => d.dismiss());
  try {
    // allauth's own login form; its field names are part of its POST API.
    await page.goto(`${BASE}/accounts/login/`);
    await page.locator("input[name=login]").fill(USER);
    await page.locator("input[name=password]").fill(PASSWORD);
    await page.locator("form button[type=submit]").first().click();
    await page.waitForURL((url) => !url.pathname.startsWith("/accounts/login"));
    // Own a kwiz, so the editor and owner-only links show up.
    await openDemoKwiz(page);
    await page.getByTestId("remix").click();
    await page.waitForURL(/\/kwizzes\/\d+\/edit\/$/);
    await page.waitForLoadState("networkidle");
  } catch (error) {
    // Crawling now would scan signed-out pages and call them signed in.
    problems.push(`signed-in setup: ${error.message.split("\n")[0]}; signed-in pages not scanned`);
    await context.close();
    return;
  }
  await crawl(page, "signed in", [
    "/kwizzes/",
    "/",
    "/kwizzes/bank/",
    "/repos/",
    "/accounts/email/",
    "/accounts/password/change/",
    "/accounts/password/set/",
    "/accounts/reauthenticate/",
    "/accounts/3rdparty/",
    "/accounts/social/connections/",
  ]);
  await context.close();
}

// Every route must be scanned or excluded, and every exclusion must still
// match a route, so this script can't quietly fall behind the app.
function checkCoverage() {
  const routes = [...new Map(ROUTES.map((r) => [r.route, r])).values()];
  for (const r of routes) {
    if (!covered.has(r.route) && !excludedBy(r)) {
      problems.push(
        `route not scanned: ${r.route}${r.name ? ` (${r.name})` : ""}. ` +
          "Link to it, add it to a crawl's start paths, or add it to a11y/exclude.json with a reason.",
      );
    }
  }
  for (const e of EXCLUDE) {
    if (!routes.some((r) => excludedBy(r) === e)) {
      problems.push(`EXCLUDE entry "${e.route}" matches no route; remove it from a11y/exclude.json`);
    }
  }
}

function cell(text) {
  return String(text).replace(/\s+/g, " ").replace(/\|/g, "\\|").trim();
}

function report() {
  // One section per rule, listing every page and element it failed on.
  const rules = new Map();
  for (const { context, url, violations } of scans) {
    for (const v of violations) {
      if (!rules.has(v.id)) rules.set(v.id, { ...v, hits: [] });
      for (const node of v.nodes) {
        rules.get(v.id).hits.push({ context, url, node });
      }
    }
  }
  const order = { critical: 0, serious: 1, moderate: 2, minor: 3 };
  const sorted = [...rules.values()].sort(
    (a, b) => (order[a.impact] ?? 4) - (order[b.impact] ?? 4) || b.hits.length - a.hits.length,
  );
  const total = sorted.reduce((sum, r) => sum + r.hits.length, 0);
  const pages = new Set(scans.filter((s) => s.violations.length).map((s) => `${s.context} ${s.url}`));

  const lines = [
    `Scanned ${scans.length} pages and screens with axe-core (tags: ${TAGS.join(", ")}).`,
    "",
  ];
  if (problems.length) {
    lines.push(
      "### Scan problems",
      "",
      "**The scan is incomplete**, so pages may be missing from the results below. " +
        "This usually means the app changed and `a11y/scan.mjs` needs updating.",
      "",
      ...problems.map((p) => `- ${p}`),
      "",
    );
  }
  lines.push(
    total
      ? `Found **${total}** failing elements across **${sorted.length}** rules on **${pages.size}** pages.`
      : "No violations found.",
    "",
  );
  if (sorted.length) {
    lines.push("| Impact | Rule | Elements | Pages |", "| --- | --- | --- | --- |");
    for (const r of sorted) {
      const ruleUrls = new Set(r.hits.map((h) => `${h.context} ${h.url}`));
      lines.push(`| ${r.impact} | [${r.id}](${r.helpUrl}): ${cell(r.help)} | ${r.hits.length} | ${ruleUrls.size} |`);
    }
    lines.push("");
  }
  for (const r of sorted) {
    lines.push(`### ${r.impact}: ${r.id}`, "", `${r.description} [How to fix](${r.helpUrl})`, "");
    lines.push("| Page | Element | Problem |", "| --- | --- | --- |");
    for (const { context, url, node } of r.hits.slice(0, 50)) {
      const html = node.html.length > 120 ? `${node.html.slice(0, 117)}...` : node.html;
      const why = node.failureSummary?.replace(/^Fix (any|all) of the following:\s*/i, "") ?? "";
      lines.push(`| ${cell(context)}: \`${cell(url)}\` | \`${cell(html)}\` | ${cell(why)} |`);
    }
    if (r.hits.length > 50) lines.push(`| … | ${r.hits.length - 50} more | |`);
    lines.push("");
  }
  lines.push("<details><summary>Pages scanned</summary>", "");
  for (const s of scans) {
    const n = s.violations.reduce((sum, v) => sum + v.nodes.length, 0);
    lines.push(`- ${n ? "✗" : "✓"} ${s.context}: \`${s.url}\`${n ? ` (${n})` : ""}`);
  }
  lines.push("", "</details>", "", "<details><summary>Routes not scanned on purpose</summary>", "");
  for (const e of EXCLUDE) lines.push(`- \`${e.route}\`: ${e.why}`);
  lines.push("", "</details>", "");
  return { markdown: lines.join("\n"), total };
}

const browser = await chromium.launch();
try {
  const anonymous = await browser.newContext();
  const page = await anonymous.newPage();
  page.on("dialog", (d) => d.dismiss());
  const editLinks = await signedOutSetup(page);
  await crawl(page, "signed out", [
    ...editLinks,
    "/",
    "/play/",
    "/kwizzes/library/",
    "/kwizzes/new/",
    "/kwizzes/import/",
    "/accounts/login/",
    "/accounts/signup/",
    "/accounts/inactive/",
    "/accounts/confirm-email/",
    "/accounts/confirm-email/not-a-real-key/",
    "/accounts/password/reset/",
    "/accounts/password/reset/done/",
    "/accounts/password/reset/key/0-not-a-real-key/",
    "/accounts/password/reset/key/done/",
    "/accounts/3rdparty/login/cancelled/",
    "/accounts/3rdparty/login/error/",
    "/accounts/social/login/cancelled/",
    "/accounts/social/login/error/",
  ]);
  await notFound(page);
  await anonymous.close();
  await signedIn(browser);
  await playGame(browser);
} finally {
  await browser.close();
}
checkCoverage();

const { markdown, total } = report();
await writeFile("a11y-report.md", markdown);
await writeFile(
  "a11y-results.json",
  JSON.stringify({ total, complete: !problems.length, problems, scans }, null, 2),
);
console.log(`\n${total} failing elements across ${scans.length} pages. See a11y-report.md.`);
if (!scans.length) process.exit(1);
if (problems.length) {
  console.error(`\nThe scan is incomplete:\n${problems.map((p) => `  - ${p}`).join("\n")}`);
  process.exit(2);
}
