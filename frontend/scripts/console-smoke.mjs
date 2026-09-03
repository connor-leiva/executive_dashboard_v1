import { spawn } from "node:child_process";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const chromePaths = [
  process.env.CHROME_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(Boolean);

const cdpPort = Number(process.env.CDP_PORT || 9337);
const consoleUrl = process.env.CONSOLE_URL || "http://127.0.0.1:5175/console/";
const loginUrl = process.env.LOGIN_URL || "http://127.0.0.1:8000/api/v1/auth/login";
const tenantHost = process.env.TENANT_HOST || "utah-life.acumyn.io";
const email = process.env.CONSOLE_EMAIL || "justin@utahliferealestate.com";
const password = process.env.CONSOLE_PASSWORD || "password123";
const expectedText = (process.env.SMOKE_EXPECT || "Overview|Setup Checklist|Recent Activity|People|Courses|SOPs")
  .split("|")
  .map((value) => value.trim())
  .filter(Boolean);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function findChrome() {
  const { access } = await import("node:fs/promises");
  for (const path of chromePaths) {
    try {
      await access(path);
      return path;
    } catch {
      continue;
    }
  }
  throw new Error("No Chrome or Edge binary found.");
}

async function cdpTarget() {
  for (let i = 0; i < 30; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${cdpPort}/json/list`);
      if (res.ok) {
        const targets = await res.json();
        const target = targets.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
        if (target) return target;
      }
    } catch {
      await sleep(200);
    }
  }
  throw new Error("Chrome page target did not start.");
}

function connect(url, onEvent) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const pending = new Map();
    let id = 0;
    ws.onopen = () => {
      resolve((method, params = {}) => new Promise((res, rej) => {
        id += 1;
        pending.set(id, { res, rej });
        ws.send(JSON.stringify({ id, method, params }));
      }));
    };
    ws.onerror = reject;
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (!msg.id) {
        onEvent?.(msg);
        return;
      }
      if (!msg.id || !pending.has(msg.id)) return;
      const next = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) next.rej(new Error(msg.error.message));
      else next.res(msg.result || {});
    };
  });
}

async function login() {
  const res = await fetch(loginUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Tenant-Host": tenantHost },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(`login failed ${res.status}`);
  return res.json();
}

const chrome = await findChrome();
const profile = await mkdtemp(join(tmpdir(), "ul-console-chrome-"));
const proc = spawn(chrome, [
  "--headless=new",
  `--remote-debugging-port=${cdpPort}`,
  `--user-data-dir=${profile}`,
  "--disable-gpu",
  "--no-first-run",
  "about:blank",
], { stdio: "ignore" });

try {
  const target = await cdpTarget();
  const events = [];
  const cdp = await connect(target.webSocketDebuggerUrl, (event) => {
    if (event.method === "Runtime.exceptionThrown" || event.method === "Log.entryAdded") {
      events.push(event);
    }
  });
  await cdp("Page.enable");
  await cdp("Runtime.enable");
  await cdp("Log.enable");
  await cdp("Page.navigate", { url: consoleUrl });
  await sleep(1000);
  const { token } = await login();
  await cdp("Runtime.evaluate", {
    expression: [
      `localStorage.setItem('cc_token', ${JSON.stringify(token)})`,
      `localStorage.setItem('cc_console_tenant_host', ${JSON.stringify(tenantHost)})`,
      "location.reload()",
    ].join(";"),
  });
  let text = "";
  for (let i = 0; i < 20; i += 1) {
    await sleep(500);
    const result = await cdp("Runtime.evaluate", {
      expression: "document.querySelector('.console-content')?.innerText || document.body.innerText",
      returnByValue: true,
    });
    text = result.result.value || "";
    if (expectedText.some((value) => text.includes(value)) || text.includes("Console data failed")) break;
  }
  const probe = await cdp("Runtime.evaluate", {
    expression: `fetch('http://localhost:8000/api/console/overview', { headers: { Authorization: 'Bearer ' + localStorage.getItem('cc_token'), 'X-Tenant-Host': localStorage.getItem('cc_console_tenant_host') } }).then(async (res) => ({ status: res.status, text: (await res.text()).slice(0, 120) })).catch((err) => ({ error: String(err) }))`,
    awaitPromise: true,
    returnByValue: true,
  });
  console.log("BROWSER FETCH PROBE");
  console.log(JSON.stringify(probe.result.value, null, 2));
  const content = await cdp("Runtime.evaluate", {
    expression: "document.querySelector('.console-content')?.innerHTML || ''",
    returnByValue: true,
  });
  const shell = await cdp("Runtime.evaluate", {
    expression: "document.body.innerText",
    returnByValue: true,
  });
  const assertionText = `${shell.result.value || ""}\n${text}\n${content.result.value || ""}`;
  console.log("CONTENT HTML");
  console.log((content.result.value || "").slice(0, 500));
  console.log(text.split("\n").filter(Boolean).slice(0, 45).join("\n"));
  if (events.length) {
    console.log("BROWSER EVENTS");
    console.log(JSON.stringify(events.slice(0, 6), null, 2));
  }
  for (const value of expectedText) {
    if (!assertionText.includes(value)) throw new Error(`missing ${value}`);
  }
} finally {
  proc.kill();
}
