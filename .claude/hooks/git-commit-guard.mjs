#!/usr/bin/env node
import { createInterface } from "readline";
import { execSync } from "child_process";

const BLOCK_PREFIXES = [
  "node_modules/",
  ".venv/",
  "__pycache__/",
  ".pytest_cache/",
  "dist/",
  "build/",
];

const BLOCK_PATH_RE = [
  /^\.env$/,
  /^\.env\.(?!example$).+$/,
  /\.pem$/,
  /\.key$/,
  /session_cookies\.json$/,
];

const BLOCK_MSG_RE = /^\s*(wip|temp|misc|asdf|\.+|x+|#+|-+)\s*$/i;

const CRITICAL_EXACT = new Set([
  ".claude/settings.json",
  ".gitignore",
  "pyproject.toml",
  "requirements.txt",
  "requirements-dev.txt",
  ".env.example",
  "README.md",
]);

const CRITICAL_PREFIX_RE = [/^\.claude\/hooks\//, /^\.claude\/rules\//];

function isCritical(file) {
  if (CRITICAL_EXACT.has(file)) return true;
  if (CRITICAL_PREFIX_RE.some((r) => r.test(file))) return true;
  return false;
}

const AREA_RULES = [
  { name: "Src", test: (f) => f.startsWith("src/") },
  { name: "Scripts", test: (f) => f.startsWith("scripts/") },
  { name: "Data", test: (f) => f.startsWith("data/") },
  { name: "Tests", test: (f) => f.startsWith("tests/") || f.startsWith("test/") },
  { name: "Config/Infra", test: (f) => f.startsWith(".claude/") || isCritical(f) },
];

function classifyAreas(files) {
  const areas = new Set();
  for (const file of files) {
    for (const rule of AREA_RULES) {
      if (rule.test(file)) {
        areas.add(rule.name);
        break;
      }
    }
  }
  return [...areas];
}

const DEBUG_RE = /\bprint\s*\(|#\s*TODO\b|#\s*FIXME\b|import\s+pdb|pdb\.set_trace/;
const SOURCE_EXTS = new Set([".py"]);

function extOf(file) {
  const m = file.match(/(\.[^./]+)$/);
  return m ? m[1] : "";
}

function runGit(cmd) {
  try {
    return execSync(cmd, { encoding: "utf8" }).trim();
  } catch {
    return "";
  }
}

function extractCommitMessage(command) {
  const mFlag = command.match(/-m\s+["']([^"']+)["']/);
  if (mFlag) return mFlag[1].trim();
  const catHeredoc = command.match(/cat\s+<<['"]*\w+['"]*\n([\s\S]*?)\n\s*\w+\s*\)/);
  if (catHeredoc) return catHeredoc[1].trim();
  const heredoc = command.match(/<<['"]*\w+['"]*\n([\s\S]*?)\n\w+/);
  if (heredoc) return heredoc[1].trim();
  return null;
}

function block(reason) {
  process.stderr.write(`[commit-guard] BLOCKED\n${reason}\n`);
  process.exit(2);
}

function ask(lines) {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "ask",
        permissionDecisionReason: `[commit-guard] Confirm commit\n${lines.join("\n")}`,
      },
    })
  );
  process.exit(0);
}

function warn(lines) {
  process.stderr.write(`[commit-guard] Warning\n${lines.join("\n")}\n`);
  process.exit(0);
}

const rl = createInterface({ input: process.stdin });
let raw = "";
rl.on("line", (line) => (raw += line));
rl.on("close", () => {
  const input = JSON.parse(raw || "{}");
  const command = input.tool_input?.command || "";

  if (!/\bgit\s+commit\b/.test(command)) process.exit(0);

  const stagedRaw = runGit("git diff --cached --name-status");
  const stagedDiff = runGit("git diff --cached --unified=0");

  const allEntries = stagedRaw
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [status, ...parts] = line.split("\t");
      const file = parts[parts.length - 1] || "";
      return { status: status[0], file };
    });

  const meaningfulFiles = allEntries.filter((e) => e.status !== "D").map((e) => e.file);
  const allFiles = allEntries.map((e) => e.file);
  const commitMessage = extractCommitMessage(command);
  const bypassed = commitMessage?.includes("[commit-guard-ignore]") ?? false;

  // Tier 1 — Hard block
  if (commitMessage !== null) {
    if (!commitMessage.replace(/\[commit-guard-ignore\]/g, "").trim()) {
      block("Commit message is empty.");
    }
    if (BLOCK_MSG_RE.test(commitMessage.replace(/\[commit-guard-ignore\]/g, "").trim())) {
      block(`Bad commit message: "${commitMessage}"`);
    }
  }

  for (const file of allFiles) {
    for (const prefix of BLOCK_PREFIXES) {
      if (file.startsWith(prefix)) block(`Dangerous path staged: ${file}`);
    }
    for (const re of BLOCK_PATH_RE) {
      if (re.test(file)) block(`Dangerous file staged: ${file}`);
    }
  }

  // Tier 2 — Ask: critical files
  const criticalFiles = allFiles.filter(isCritical);
  if (criticalFiles.length > 0) {
    const areas = classifyAreas(meaningfulFiles);
    const lines = [
      `Critical files:`,
      ...criticalFiles.map((f) => `  * ${f}`),
      `Areas:`,
      ...areas.map((a) => `  * ${a}`),
      `Files: ${meaningfulFiles.length}`,
    ];
    if (commitMessage)
      lines.push(`Message: "${commitMessage.replace(/\[commit-guard-ignore\]/g, "").trim()}"`);
    ask(lines);
  }

  // Tier 3 — Warn (bypassable)
  if (bypassed) process.exit(0);

  const areas = classifyAreas(meaningfulFiles);
  const count = meaningfulFiles.length;
  const warnings = [];

  if (count > 40) warnings.push(`Very large commit: ${count} files.`);
  else if (count > 25) warnings.push(`Large commit: ${count} files.`);

  if (areas.length >= 4) warnings.push(`Spans ${areas.length} areas: ${areas.join(", ")}.`);
  else if (areas.length >= 3) warnings.push(`Spans multiple areas: ${areas.join(", ")}.`);

  const debugHits = new Set();
  let currentFile = "";
  for (const line of stagedDiff.split("\n")) {
    if (line.startsWith("+++ b/")) {
      currentFile = line.slice(6);
    } else if (line.startsWith("+") && !line.startsWith("+++")) {
      if (SOURCE_EXTS.has(extOf(currentFile)) && DEBUG_RE.test(line)) {
        debugHits.add(currentFile);
      }
    }
  }
  if (debugHits.size > 0) warnings.push(`Debug artifacts in: ${[...debugHits].join(", ")}`);

  if (warnings.length > 0) {
    const lines = [
      ...warnings.map((w) => `  ! ${w}`),
      `  Files: ${count}  Areas: ${areas.join(", ")}`,
      `  (add [commit-guard-ignore] to message to skip these warnings)`,
    ];
    warn(lines);
  }

  process.exit(0);
});
