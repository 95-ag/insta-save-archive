#!/usr/bin/env node
import { createInterface } from "readline";
import path from "path";

const rl = createInterface({ input: process.stdin });
let raw = "";
rl.on("line", (line) => (raw += line));
rl.on("close", () => {
  const input = JSON.parse(raw || "{}");
  const filePath = input.tool_input?.file_path || "";
  const projectRoot = process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const rel = path.relative(projectRoot, path.resolve(projectRoot, filePath));

  const HARD_BLOCK = [/^\.git(\/|$)/, /^\.env(\.|$)/];

  const ASK = [
    /^\.claude(\/|$)/,
    /^CLAUDE\.md$/,
    /^AGENTS\.md$/,
    /^\.gitignore$/,
    /^package\.json$/,
    /^biome\.json$/,
    /^tsconfig\.json$/,
    /^next\.config\.ts$/,
  ];

  // Global / out-of-repo config: needs explicit approval regardless of project-relative path.
  // Matches WSL & Windows user-global Claude config, login/shell dotfiles, and /etc.
  const abs = String(filePath).replace(/\\/g, "/");
  const GLOBAL_ASK = [
    /\/home\/[^/]+\/\.claude(\/|$)/i,
    /\/Users\/[^/]+\/\.claude(\/|$)/i,
    /(^|\/)(\.profile|\.bash_profile|\.bash_login|\.bashrc|\.zshrc|\.zprofile)$/i,
    /^\/etc\//i,
  ];

  for (const pattern of HARD_BLOCK) {
    if (pattern.test(rel)) {
      process.stderr.write(
        `[file-protect] BLOCKED: "${rel}" is a protected file and cannot be edited.\n`,
      );
      process.exit(2);
    }
  }

  for (const pattern of GLOBAL_ASK) {
    if (pattern.test(abs)) {
      process.stdout.write(
        JSON.stringify({
          hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "ask",
            permissionDecisionReason: `"${filePath}" is global / out-of-repo config. Global-config changes need explicit approval — confirm this edit is intentional.`,
          },
        }),
      );
      process.exit(0);
    }
  }

  for (const pattern of ASK) {
    if (pattern.test(rel)) {
      process.stdout.write(
        JSON.stringify({
          hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "ask",
            permissionDecisionReason: `"${rel}" is a sensitive config/rules file. Confirm this edit is intentional.`,
          },
        }),
      );
      process.exit(0);
    }
  }

  process.exit(0);
});
