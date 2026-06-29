#!/usr/bin/env node
/**
 * Memory auto-update harvester — scans Claude session transcripts, extracts deltas
 * with a cheap model, upserts via local grok-mcp memory_upsert.
 *
 * Dry-run: MEMORY_HARVEST_DRYRUN=1 (writes candidates to harvest-dryrun.log, no upsert)
 */
import { readFileSync, writeFileSync, mkdirSync, appendFileSync, existsSync } from "node:fs";
import { readdir, readFile } from "node:fs/promises";
import { join, basename } from "node:path";
import { execSync } from "node:child_process";

const REPO = process.argv[2] || "/root/astra-config";
const ENV_FILE = "/etc/grok-mcp.env";
const STATE_DIR = "/root/.local/state/grok-mcp";
const STATE_FILE = join(STATE_DIR, "memory-harvest.json");
const DRYRUN_LOG = join(STATE_DIR, "harvest-dryrun.log");
const NODE = "/root/.nvm/versions/node/v22.22.3/bin/node";
const TRANSCRIPT_BASE = "/root/.claude/projects/-root";
const LIVE_SESSION_MS = 15 * 60 * 1000;
const DELTA_CAP = 20_000;
const DRYRUN = process.env.MEMORY_HARVEST_DRYRUN === "1";

function containsSecretLeak(text) {
  return /tail[a-z0-9-]+\.ts\.net\/mcp|sk-or-v1-|xai-[a-zA-Z0-9]{8,}|sk-ant-oat|MCP_PATH\s*=|\/etc\/grok-mcp\.env/i.test(text || "");
}

function stamp() { return new Date().toISOString(); }
function log(msg) { console.error(`[memory-harvest] ${stamp()} ${msg}`); }

function loadEnv() {
  let raw = "";
  try { raw = readFileSync(ENV_FILE, "utf8"); } catch {}
  const mcpPath = (raw.match(/^MCP_PATH=(.+)/m)?.[1] || "/mcp").split(",")[0].trim();
  const orKey = raw.match(/^OPENROUTER_API_KEY=(.+)/m)?.[1]?.trim();
  if (!orKey) throw new Error(`OPENROUTER_API_KEY missing in ${ENV_FILE}`);
  return { mcpPath, orKey };
}

function mcpUrl(mcpPath) {
  return `http://127.0.0.1:3000${mcpPath}`;
}

function mcpCall(mcpPath, tool, args) {
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: Date.now(),
    method: "tools/call",
    params: { name: tool, arguments: args },
  });
  const url = mcpUrl(mcpPath);
  const out = execSync(
    `curl -s --max-time 30 -X POST "${url}" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '${body.replace(/'/g, "'\\''")}'`,
    { encoding: "utf8" },
  );
  const line = out.split(/\r?\n/).find(l => l.startsWith("data:"));
  if (!line) throw new Error(`MCP ${tool}: no SSE data line`);
  const j = JSON.parse(line.slice(5).trim());
  if (j.error) throw new Error(`MCP ${tool}: ${JSON.stringify(j.error)}`);
  const text = j.result?.content?.[0]?.text;
  if (!text) throw new Error(`MCP ${tool}: empty result`);
  return JSON.parse(text);
}

async function globTranscripts() {
  const paths = [];
  let entries;
  try {
    entries = await readdir(TRANSCRIPT_BASE, { withFileTypes: true });
  } catch {
    return paths;
  }
  for (const e of entries) {
    if (e.isFile() && e.name.endsWith(".jsonl")) {
      paths.push(join(TRANSCRIPT_BASE, e.name));
      continue;
    }
    if (e.isDirectory()) {
      const sub = join(TRANSCRIPT_BASE, e.name, "subagents");
      try {
        for (const s of await readdir(sub, { withFileTypes: true })) {
          if (s.isFile() && s.name.endsWith(".jsonl")) paths.push(join(sub, s.name));
        }
      } catch {}
    }
  }
  return paths;
}

function loadState() {
  mkdirSync(STATE_DIR, { recursive: true });
  if (!existsSync(STATE_FILE)) return { processed: {} };
  try {
    return JSON.parse(readFileSync(STATE_FILE, "utf8"));
  } catch {
    return { processed: {} };
  }
}

function saveState(state) {
  writeFileSync(STATE_FILE, JSON.stringify(state, null, 2) + "\n", "utf8");
}

function parseJsonl(raw) {
  const rows = [];
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      rows.push(JSON.parse(line));
    } catch (e) {
      throw new Error(`JSONL parse failure: ${e.message}`);
    }
  }
  return rows;
}

function extractTextContent(message) {
  if (!message?.content) return "";
  if (typeof message.content === "string") return message.content;
  if (!Array.isArray(message.content)) return "";
  return message.content
    .filter(b => b.type === "text" && typeof b.text === "string")
    .map(b => b.text)
    .join("\n");
}

function extractToolUses(message) {
  if (!message?.content || !Array.isArray(message.content)) return [];
  return message.content
    .filter(b => b.type === "tool_use")
    .map(b => ({ name: b.name, input: b.input || {} }));
}

function isReadOnlyBash(cmd) {
  return /^\s*(ls|cat|head|tail|grep|rg|find|wc|echo|pwd|which|git\s+(status|log|diff|show|branch)|npm\s+test|node\s+test\/|sleep)\b/.test(cmd || "");
}

function isEffectingBash(cmd) {
  if (!cmd || typeof cmd !== "string") return false;
  const first = cmd.trim().split(/\s*&&\s*/)[0];
  if (isReadOnlyBash(first)) return false;
  return true;
}

function rowSubstantive(row) {
  if (row.type === "user") {
    const text = extractTextContent(row.message);
    return text.length > 0;
  }
  if (row.type !== "assistant") return false;
  const tools = extractToolUses(row.message);
  for (const t of tools) {
    if (["Edit", "Write", "NotebookEdit"].includes(t.name)) return true;
    if (t.name === "Bash" && isEffectingBash(t.input?.command)) return true;
  }
  const text = extractTextContent(row.message);
  if (text.length < 80) return false;
  return /\b(decision|deployed|gotcha|config|architecture|fix(?:ed)?|non-negotiable|rotation|failover)\b/i.test(text);
}

function buildDelta(rows) {
  const parts = [];
  for (const row of rows) {
    if (row.type === "user") {
      const text = extractTextContent(row.message).slice(0, 2000);
      if (text) parts.push(`USER: ${text}`);
    } else if (row.type === "assistant") {
      const text = extractTextContent(row.message).slice(0, 1500);
      if (text) parts.push(`ASSISTANT: ${text}`);
      for (const t of extractToolUses(row.message)) {
        if (t.name === "Edit" || t.name === "Write" || t.name === "NotebookEdit") {
          const path = t.input?.file_path || t.input?.target_notebook || t.input?.notebook_path || "?";
          parts.push(`TOOL: ${t.name} ${path}`);
        } else if (t.name === "Bash" && isEffectingBash(t.input?.command)) {
          parts.push(`TOOL: Bash ${(t.input.command || "").slice(0, 200)}`);
        }
      }
    }
  }
  let delta = parts.join("\n---\n");
  if (delta.length > DELTA_CAP) delta = delta.slice(-DELTA_CAP);
  return delta;
}

function sessionQualifies(rows) {
  return rows.some(r => r.type === "assistant" && rowSubstantive(r));
}

async function extractCandidates(orKey, delta, existingFacts) {
  const existingList = existingFacts
    .slice(0, 120)
    .map(f => `${f.id}: ${f.description}`)
    .join("\n");

  const prompt = `You are performing memory auto-update for a shared knowledge base.
Existing fact ids (do not duplicate): 
${existingList}

Session delta (only what was newly learned):
${delta}

Rules:
- HIGH CONFIDENCE ONLY. Novel + useful + not obvious + not already in the list. If nothing qualifies, return [].
- Prefer UPDATING an existing fact (reuse its id as name) over creating a near-dup.
- Output 0-3 items: {name (kebab id), description (one line), tags (from the canonical set: grok-mcp, astra-config, memory, infra, gotcha, decision, reference, feedback, ops, security, workspace, vps, claude-code, historical), related (ids), content (markdown body, no frontmatter), conflicts_with?}.
- If an item CONTRADICTS an existing fact, set conflicts_with:"<id>" and do NOT assert the new claim as settled.
- Cost low. Delta only.`;

  const schema = {
    type: "array",
    maxItems: 3,
    items: {
      type: "object",
      properties: {
        name: { type: "string" },
        description: { type: "string" },
        tags: { type: "array", items: { type: "string" } },
        related: { type: "array", items: { type: "string" } },
        content: { type: "string" },
        conflicts_with: { type: "string" },
      },
      required: ["name", "description", "tags", "content"],
      additionalProperties: false,
    },
  };

  async function callModel(model) {
    const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${orKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model,
        messages: [{ role: "user", content: prompt }],
        response_format: {
          type: "json_schema",
          json_schema: { name: "memory_candidates", strict: true, schema },
        },
      }),
    });
    if (!res.ok) throw new Error(`OpenRouter ${model} http ${res.status}`);
    const j = await res.json();
    const text = j.choices?.[0]?.message?.content;
    if (!text) throw new Error(`OpenRouter ${model}: empty content`);
    return JSON.parse(text);
  }

  try {
    return await callModel("google/gemini-3.1-flash-lite");
  } catch (e1) {
    log(`flash-lite failed (${e1.message}), trying 2.5-flash-lite`);
    return await callModel("google/gemini-2.5-flash-lite");
  }
}

function notify(subject, body) {
  try {
    execSync(`bash "${join(REPO, "scripts/notify-email.sh")}" "${subject.replace(/"/g, '\\"')}"`, {
      input: body,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
    });
  } catch (e) {
    log(`notify failed: ${e.message}`);
  }
}

async function main() {
  const { mcpPath, orKey } = loadEnv();
  const state = loadState();
  const paths = await globTranscripts();
  log(`scanning ${paths.length} transcript(s) dryrun=${DRYRUN}`);

  const listRes = await mcpCall(mcpPath, "memory_list", { limit: 200 });
  const existingFacts = listRes.facts || [];

  let harvested = 0;
  let skipped = 0;

  for (const filePath of paths) {
    const sessionId = basename(filePath, ".jsonl");
    const raw = await readFile(filePath, "utf8");
    const allRows = parseJsonl(raw);

    const newestTs = allRows.reduce((m, r) => {
      const t = r.timestamp ? Date.parse(r.timestamp) : 0;
      return t > m ? t : m;
    }, 0);
    if (newestTs && Date.now() - newestTs < LIVE_SESSION_MS) {
      log(`skip live session ${sessionId}`);
      continue;
    }

    const cursor = state.processed[sessionId] || "";
    const newRows = allRows.filter(r => {
      if (r.type !== "user" && r.type !== "assistant") return false;
      if (!r.timestamp) return false;
      return r.timestamp > cursor;
    });

    if (newRows.length === 0) continue;

    if (!sessionQualifies(newRows)) {
      const lastTs = newRows[newRows.length - 1]?.timestamp;
      if (lastTs) state.processed[sessionId] = lastTs;
      skipped++;
      continue;
    }

    const delta = buildDelta(newRows);
    if (!delta.trim()) {
      const lastTs = newRows[newRows.length - 1]?.timestamp;
      if (lastTs) state.processed[sessionId] = lastTs;
      skipped++;
      continue;
    }

    let candidates;
    try {
      candidates = await extractCandidates(orKey, delta, existingFacts);
    } catch (e) {
      log(`extraction failed for ${sessionId}: ${e.message}`);
      notify("memory-harvest extraction failed", `session=${sessionId}\n${e.message}`);
      continue;
    }

    if (!Array.isArray(candidates)) {
      notify("memory-harvest schema breakage", `session=${sessionId}: non-array response`);
      throw new Error("extractor returned non-array — fail loud");
    }

    for (const c of candidates.slice(0, 3)) {
      if (!c.name || !c.content) continue;
      if (containsSecretLeak(c.content) || containsSecretLeak(c.description)) {
        log(`skip secret-leak candidate ${c.name}`);
        continue;
      }

      let targetId = c.name.replace(/\.md$/, "");
      const searchRes = await mcpCall(mcpPath, "memory_search", {
        query: c.name,
        tags: c.tags?.length ? c.tags.slice(0, 2) : undefined,
        limit: 5,
      });
      const match = (searchRes.facts || []).find(f =>
        f.id === targetId || f.name === c.name ||
        (c.description && f.description === c.description),
      );
      if (match) targetId = match.id;

      const payload = {
        name: targetId,
        description: c.description || "",
        content: c.conflicts_with
          ? `**CONFLICT NOTE:** disagrees with [[${c.conflicts_with}]]\n\n${c.content}`
          : c.content,
        tags: [...new Set([...(c.tags || []), ...(c.conflicts_with ? ["needs-review"] : [])])],
        related: c.related || [],
      };

      if (DRYRUN) {
        appendFileSync(DRYRUN_LOG, `\n=== ${stamp()} session=${sessionId} ===\n${JSON.stringify(payload, null, 2)}\n`);
        log(`dry-run candidate: ${targetId}`);
      } else if (c.conflicts_with) {
        const up = await mcpCall(mcpPath, "memory_upsert", payload);
        notify(
          "memory-harvest conflict flagged",
          `fact=${targetId} conflicts_with=${c.conflicts_with}\n${c.description}`,
        );
        log(`upsert conflict ${targetId} (v${up.version})`);
      } else {
        const up = await mcpCall(mcpPath, "memory_upsert", payload);
        log(`upsert ${targetId} (v${up.version})`);
      }
      harvested++;
    }

    const lastTs = newRows[newRows.length - 1]?.timestamp;
    if (lastTs) state.processed[sessionId] = lastTs;
  }

  saveState(state);
  log(`done harvested=${harvested} skipped=${skipped} sessions_tracked=${Object.keys(state.processed).length}`);
}

main().catch(e => {
  log(`FATAL: ${e.message}`);
  notify("memory-harvest fatal", e.message);
  process.exit(1);
});