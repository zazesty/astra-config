#!/usr/bin/env node
// Thin helper to upsert into the memory KB via the local grok-mcp (no secret in code).
// Usage (from agent or script):
//   echo 'name: my-fact
// description: one line
// tags: grok-mcp gotcha
// content: |+
//   Full body here with new learning.
// ' | node astra-config/scripts/memory-upsert.mjs
//
// Or pass JSON on stdin with {name, description?, tags?, content}

import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";

const envFile = "/etc/grok-mcp.env";
let rawEnv = "";
try { rawEnv = readFileSync(envFile, "utf8"); } catch {}
const mcpPath = (rawEnv.match(/^MCP_PATH=(.+)/m)?.[1] || "/mcp").split(",")[0].trim();

const input = readFileSync(0, "utf8").trim();
if (!input) {
  console.error("usage: feed frontmatter+content or JSON to stdin");
  process.exit(1);
}

let payload;
try {
  payload = JSON.parse(input);
} catch {
  // simple key: value + content: block parser
  const lines = input.split(/\r?\n/);
  payload = { tags: [], related: [] };
  let inContent = false;
  let contentLines = [];
  for (const line of lines) {
    if (/^content:\s*\|?/.test(line)) { inContent = true; continue; }
    if (inContent) { contentLines.push(line); continue; }
    const kv = line.match(/^([a-z_]+):\s*(.+)$/i);
    if (kv) {
      const k = kv[1].toLowerCase();
      if (k === "tags" || k === "related") payload[k] = kv[2].split(/[\s,]+/).filter(Boolean);
      else payload[k] = kv[2];
    }
  }
  payload.content = contentLines.join("\n").trim();
}

if (!payload.name || !payload.content) {
  console.error("need at least name + content");
  process.exit(1);
}

const body = JSON.stringify({
  jsonrpc: "2.0",
  id: Date.now(),
  method: "tools/call",
  params: {
    name: "memory_upsert",
    arguments: {
      name: payload.name,
      description: payload.description || "",
      content: payload.content,
      tags: payload.tags || [],
      related: payload.related || [],
    },
  },
});

const url = `http://127.0.0.1:3000${mcpPath}`;
try {
  const out = execSync(`curl -s --max-time 15 -X POST "${url}" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '${body.replace(/'/g, "'\\''")}'`, { encoding: "utf8" });
  const line = out.split(/\r?\n/).find(l => l.startsWith("data:"));
  const j = line ? JSON.parse(line.slice(5).trim()) : {};
  const result = j.result?.content?.[0]?.text || out;
  console.log(result);
} catch (e) {
  console.error("upsert failed:", e.message);
  process.exit(1);
}
