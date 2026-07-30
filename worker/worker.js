/**
 * NaijaJobs webhook worker — answers Telegram commands instantly.
 *
 * Telegram pushes every message here via webhook. Job data comes from the
 * data/jobs.json blob the scraper commits every ~5 minutes; user settings
 * live in data/filters.json, which this worker owns (reads AND writes via
 * the GitHub API). The Python scraper only reads filters.json — one writer
 * per file, no commit races.
 *
 * Secrets (wrangler secret put ...): TELEGRAM_BOT_TOKEN, GITHUB_TOKEN,
 * OWNER_CHAT_ID, WEBHOOK_SECRET. Vars (wrangler.toml): GH_OWNER, GH_REPO.
 */

const ATS_NAMES = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"];
const MAX_RECENT_SHOWN = 30;
const MAX_MESSAGE_CHARS = 3800;

const HELP_TEXT = `<b>NaijaJobs commands</b>
/recent [days] — matching postings from the last N days (default 3)
/filters — show current filters
/addkeyword &lt;word&gt; — title must contain one of your keywords
/delkeyword &lt;word&gt; — remove a keyword
/addlocation &lt;place&gt; — only this location (remote jobs always pass)
/dellocation &lt;place&gt; — remove a location
/remote on|off — only remote postings
/pause — stop notifications
/resume — resume notifications
/companies — list tracked companies
/addcompany &lt;ats&gt; &lt;slug&gt; — track a new company board
/delcompany &lt;ats&gt; &lt;slug&gt; — stop tracking a board
/subscribers — how many chats receive alerts
/stop — unsubscribe this chat from alerts

ATS values: ${ATS_NAMES.join(", ")}.
New-job alerts arrive within ~5 minutes of a posting going live.`;

const WELCOME_TEXT = `👋 <b>You're subscribed to job alerts!</b>
You'll receive every new posting from the tracked companies — use keyword and location filters to narrow the feed. Note: filters and the company list are shared between all subscribers during this test phase.

`;

const DEFAULT_CONFIG = {
  filters: { keywords: [], locations: [], remote_only: false, paused: false },
  subscribers: [],
  extra_companies: {},
  removed_companies: [],
};

// ---------- pure helpers (exported for tests) ----------

export function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function matches(job, filters) {
  const title = job.title.toLowerCase();
  const location = job.location.toLowerCase();
  const keywords = (filters.keywords || []).map((k) => k.toLowerCase());
  if (keywords.length && !keywords.some((k) => title.includes(k))) return false;
  if (filters.remote_only && !job.is_remote) return false;
  const locations = (filters.locations || []).map((l) => l.toLowerCase());
  if (locations.length && !job.is_remote && !locations.some((l) => location.includes(l))) {
    return false;
  }
  return true;
}

export function formatJobLine(job, withDate = false) {
  const remote = job.is_remote ? " 🌍" : "";
  const date = withDate && job.posted ? ` · 🗓 ${job.posted}` : "";
  return `• <a href="${escapeHtml(job.url)}">${escapeHtml(job.title)}</a>\n` +
    `   🏢 ${escapeHtml(job.company)} · 📍 ${escapeHtml(job.location)}${remote}${date}`;
}

export function mergedCompanies(base, config) {
  const removed = new Set(config.removed_companies || []);
  const merged = {};
  const allAts = new Set([...Object.keys(base), ...Object.keys(config.extra_companies || {})]);
  for (const ats of allAts) {
    const slugs = [...(base[ats] || [])];
    for (const s of (config.extra_companies || {})[ats] || []) {
      if (!slugs.includes(s)) slugs.push(s);
    }
    merged[ats] = slugs.filter((s) => !removed.has(`${ats}:${s}`));
  }
  return merged;
}

export function normalizeConfig(raw) {
  const config = structuredClone(DEFAULT_CONFIG);
  for (const key of Object.keys(DEFAULT_CONFIG)) {
    if (raw && raw[key] !== undefined) config[key] = raw[key];
  }
  for (const [k, v] of Object.entries(DEFAULT_CONFIG.filters)) {
    if (config.filters[k] === undefined) config.filters[k] = v;
  }
  return config;
}

// ---------- Telegram ----------

async function tg(env, method, params) {
  const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return resp.json();
}

async function sendLong(env, chatId, text) {
  const lines = text.split("\n");
  let chunk = [];
  let size = 0;
  const flush = async () => {
    if (chunk.length) {
      await tg(env, "sendMessage", {
        chat_id: chatId, text: chunk.join("\n"),
        parse_mode: "HTML", disable_web_page_preview: true,
      });
      chunk = []; size = 0;
    }
  };
  for (const line of lines) {
    if (chunk.length && size + line.length + 1 > MAX_MESSAGE_CHARS) await flush();
    chunk.push(line);
    size += line.length + 1;
  }
  await flush();
}

// ---------- GitHub data access ----------

function ghHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "naijajobs-worker",
  };
}

async function loadConfig(env) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/contents/data/filters.json`;
  const resp = await fetch(url, { headers: ghHeaders(env) });
  if (resp.status === 404) return { config: structuredClone(DEFAULT_CONFIG), sha: null };
  if (!resp.ok) throw new Error(`config read failed: ${resp.status}`);
  const body = await resp.json();
  const raw = JSON.parse(atob(body.content.replace(/\n/g, "")));
  return { config: normalizeConfig(raw), sha: body.sha };
}

async function saveConfig(env, config, sha, message) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/contents/data/filters.json`;
  const put = (theSha) => fetch(url, {
    method: "PUT",
    headers: ghHeaders(env),
    body: JSON.stringify({
      message,
      content: btoa(JSON.stringify(config, Object.keys(config).sort(), 1)),
      ...(theSha ? { sha: theSha } : {}),
    }),
  });
  let resp = await put(sha);
  if (resp.status === 409 || resp.status === 422) {
    // Lost a write race; refetch sha and retry once.
    const fresh = await loadConfig(env);
    resp = await put(fresh.sha);
  }
  if (!resp.ok) throw new Error(`config write failed: ${resp.status}`);
}

async function loadJobsBlob(env) {
  const url = `https://raw.githubusercontent.com/${env.GH_OWNER}/${env.GH_REPO}/main/data/jobs.json`;
  const resp = await fetch(url, { headers: { "User-Agent": "naijajobs-worker" } });
  if (!resp.ok) return { generated_at: null, jobs: [] };
  return resp.json();
}

async function loadBaseCompanies(env) {
  const url = `https://raw.githubusercontent.com/${env.GH_OWNER}/${env.GH_REPO}/main/companies.json`;
  const resp = await fetch(url, { headers: { "User-Agent": "naijajobs-worker" } });
  return resp.ok ? resp.json() : {};
}

// Lightweight board validation for /addcompany: just count postings.
async function countBoard(ats, slug) {
  const ua = { "User-Agent": "NaijaJobs/1.0 (job-alert bot)" };
  let resp, data;
  switch (ats) {
    case "greenhouse":
      resp = await fetch(`https://boards-api.greenhouse.io/v1/boards/${slug}/jobs`, { headers: ua });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
      return (data.jobs || []).length;
    case "lever":
      resp = await fetch(`https://api.lever.co/v0/postings/${slug}?mode=json`, { headers: ua });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
      return data.length;
    case "ashby":
      resp = await fetch(`https://api.ashbyhq.com/posting-api/job-board/${slug}`, { headers: ua });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
      return (data.jobs || []).length;
    case "workable":
      resp = await fetch(`https://apply.workable.com/api/v3/accounts/${slug}/jobs`, {
        method: "POST",
        headers: { ...ua, "Content-Type": "application/json" },
        body: JSON.stringify({ query: "" }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
      return data.total || 0;
    case "smartrecruiters":
      resp = await fetch(`https://api.smartrecruiters.com/v1/companies/${slug}/postings?limit=1`, { headers: ua });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
      return data.totalFound || 0;
    default:
      throw new Error(`unknown ATS ${ats}`);
  }
}

// ---------- command handling ----------

export async function handleCommand(text, chatId, env, deps) {
  const parts = text.trim().split(/\s+/);
  const cmd = parts[0].toLowerCase().split("@")[0];
  const args = parts.slice(1);
  const argText = args.join(" ").toLowerCase();

  const { config, sha } = await deps.loadConfig(env);
  const filters = config.filters;
  const isOwner = String(chatId) === String(env.OWNER_CHAT_ID);
  const isMember = isOwner || config.subscribers.includes(String(chatId));
  const save = (msg) => deps.saveConfig(env, config, sha, msg);

  if (cmd === "/start") {
    if (!isMember) {
      config.subscribers.push(String(chatId));
      await save(`bot: subscribe ${chatId}`);
    }
    return WELCOME_TEXT + HELP_TEXT;
  }

  if (!isMember) return null; // ignore strangers until they /start

  switch (cmd) {
    case "/help":
      return HELP_TEXT;

    case "/stop":
      if (isOwner) return "You're the owner — alerts always go to you.";
      config.subscribers = config.subscribers.filter((s) => s !== String(chatId));
      await save(`bot: unsubscribe ${chatId}`);
      return "Unsubscribed. Send /start to rejoin anytime.";

    case "/filters":
      return "<b>Current filters</b>\n" +
        `Keywords: ${filters.keywords.join(", ") || "(all titles)"}\n` +
        `Locations: ${filters.locations.join(", ") || "(anywhere)"}\n` +
        `Remote only: ${filters.remote_only ? "yes" : "no"}\n` +
        `Paused: ${filters.paused ? "yes" : "no"}`;

    case "/addkeyword": {
      if (!args.length) return "Usage: /addkeyword <word>";
      if (!filters.keywords.includes(argText)) filters.keywords.push(argText);
      await save(`bot: add keyword ${argText}`);
      return `Added keyword: ${argText}\nKeywords: ${filters.keywords.join(", ")}`;
    }

    case "/delkeyword": {
      if (!filters.keywords.includes(argText)) return `Keyword not found: ${argText}`;
      filters.keywords = filters.keywords.filter((k) => k !== argText);
      config.filters = filters;
      await save(`bot: remove keyword ${argText}`);
      return `Removed keyword: ${argText}`;
    }

    case "/addlocation": {
      if (!args.length) return "Usage: /addlocation <place>";
      if (!filters.locations.includes(argText)) filters.locations.push(argText);
      await save(`bot: add location ${argText}`);
      return `Added location: ${argText}\nLocations: ${filters.locations.join(", ")}`;
    }

    case "/dellocation": {
      if (!filters.locations.includes(argText)) return `Location not found: ${argText}`;
      filters.locations = filters.locations.filter((l) => l !== argText);
      config.filters = filters;
      await save(`bot: remove location ${argText}`);
      return `Removed location: ${argText}`;
    }

    case "/remote":
      filters.remote_only = ["on", "yes", "true", "1"].includes((args[0] || "").toLowerCase());
      await save(`bot: remote_only ${filters.remote_only}`);
      return `Remote only: ${filters.remote_only ? "on" : "off"}`;

    case "/pause":
      filters.paused = true;
      await save("bot: pause");
      return "Paused. New jobs will still be marked seen, so you won't get a flood on resume.";

    case "/resume":
      filters.paused = false;
      await save("bot: resume");
      return "Resumed. You'll get new postings from the next scrape onward.";

    case "/subscribers":
      return `${1 + config.subscribers.length} chat(s) receive alerts ` +
        `(owner + ${config.subscribers.length} subscriber(s)).`;

    case "/companies": {
      const merged = mergedCompanies(await deps.loadBaseCompanies(env), config);
      const lines = ["<b>Tracked companies</b>"];
      for (const ats of Object.keys(merged).sort()) {
        if (merged[ats].length) lines.push(`${ats}: ${merged[ats].sort().join(", ")}`);
      }
      return lines.join("\n");
    }

    case "/addcompany": {
      if (args.length !== 2) return "Usage: /addcompany <ats> <slug>";
      const [ats, slug] = [args[0].toLowerCase(), args[1].toLowerCase()];
      if (!ATS_NAMES.includes(ats)) return `Unknown ATS '${ats}'. Use one of: ${ATS_NAMES.join(", ")}`;
      let count;
      try {
        count = await deps.countBoard(ats, slug);
      } catch (e) {
        return `Couldn't fetch ${ats}/${slug} — check the slug. (${e.message})`;
      }
      if (!config.extra_companies[ats]) config.extra_companies[ats] = [];
      if (!config.extra_companies[ats].includes(slug)) config.extra_companies[ats].push(slug);
      config.removed_companies = config.removed_companies.filter((k) => k !== `${ats}:${slug}`);
      await save(`bot: track ${ats}/${slug}`);
      return `Now tracking ${ats}/${slug} — ${count} current postings will be baselined ` +
        "on the next scrape; you'll be alerted about new ones.";
    }

    case "/delcompany": {
      if (args.length !== 2) return "Usage: /delcompany <ats> <slug>";
      const [ats, slug] = [args[0].toLowerCase(), args[1].toLowerCase()];
      const key = `${ats}:${slug}`;
      if ((config.extra_companies[ats] || []).includes(slug)) {
        config.extra_companies[ats] = config.extra_companies[ats].filter((s) => s !== slug);
      } else if (!config.removed_companies.includes(key)) {
        config.removed_companies.push(key);
      }
      await save(`bot: untrack ${key}`);
      return `Stopped tracking ${ats}/${slug}.`;
    }

    case "/recent": {
      let days = /^\d+$/.test(args[0] || "") ? parseInt(args[0], 10) : 3;
      days = Math.max(1, Math.min(days, 30));
      const cutoffMs = Date.now() - days * 86400_000;
      const cutoff = new Date(cutoffMs).toISOString().slice(0, 10);
      const blob = await deps.loadJobsBlob(env);
      const recent = blob.jobs
        .filter((j) => matches(j, filters) && j.posted && j.posted >= cutoff)
        .sort((a, b) => (a.posted < b.posted ? 1 : -1));
      if (!recent.length) return `No matching postings from the last ${days} day(s).`;
      let header = `🕑 <b>${recent.length} matching posting(s) from the last ${days} day(s)</b>`;
      if (recent.length > MAX_RECENT_SHOWN) header += ` — showing newest ${MAX_RECENT_SHOWN}`;
      return [header, ...recent.slice(0, MAX_RECENT_SHOWN).map((j) => formatJobLine(j, true))]
        .join("\n");
    }

    default:
      return "Unrecognized command. Send /help for the list.";
  }
}

const LIVE_DEPS = { loadConfig, saveConfig, loadJobsBlob, loadBaseCompanies, countBoard };

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("jobbot webhook", { status: 200 });
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }
    const message = update.message || update.edited_message;
    const text = message?.text || "";
    const chatId = message?.chat?.id;
    if (chatId && text.startsWith("/")) {
      // Reply asynchronously; always ACK Telegram fast so it never retries.
      ctx.waitUntil((async () => {
        try {
          const reply = await handleCommand(text, chatId, env, LIVE_DEPS);
          if (reply) await sendLong(env, chatId, reply);
        } catch (e) {
          await tg(env, "sendMessage", {
            chat_id: chatId,
            text: `⚠️ Something went wrong handling that command: ${escapeHtml(e.message)}`,
            parse_mode: "HTML",
          });
        }
      })());
    }
    return new Response("ok", { status: 200 });
  },
};
