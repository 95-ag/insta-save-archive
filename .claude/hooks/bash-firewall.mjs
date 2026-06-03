#!/usr/bin/env node
import { createInterface } from 'readline'

const HARD_BLOCK = {
  destructiveGit: [
    /\bgit\s+push\s+.*--force\b/,
    /\bgit\s+push\s+.*\s-f\b/,
    /\bgit\s+reset\s+--hard\b/,
    /\bgit\s+checkout\s+--\b/,
    /\bgit\s+clean\s+-[a-z]*f/,
    /\bgit\s+branch\s+-D\b/,
  ],
  filesystem: [
    /\brm\s+-[a-z]*r[a-z]*f\b/,
    /\brm\s+-[a-z]*f[a-z]*r\b/,
    /\bfind\b.*-delete\b/,
    /\bsudo\b/,
  ],
  bypass: [
    /--no-verify\b/,
    /--no-gpg-sign\b/,
  ],
  pipeToShell: [
    /\bcurl\b[^|#\n]*\|\s*(ba)?sh\b/,
    /\bwget\b[^|#\n]*\|\s*(ba)?sh\b/,
  ],
}

const ASK = {
  deployment: [
    /\bvercel\b.*--prod\b/,
  ],
  publishing: [
    /\bnpm\s+publish\b/,
    /\bgit\s+tag\b/,
  ],
  packageMutations: [
    /\bnpm\s+(install|uninstall|remove)\b/,
    /\bpnpm\s+(add|install|remove)\b/,
    /\byarn\s+(add|remove)\b/,
  ],
  inPlaceEdit: [
    /\bsed\s+-i\b/,
    /\bperl\s+-pi\b/,
  ],
}

const rl = createInterface({ input: process.stdin })
let raw = ''
rl.on('line', line => (raw += line))
rl.on('close', () => {
  const input = JSON.parse(raw || '{}')
  const command = input.tool_input?.command || ''

  for (const [category, patterns] of Object.entries(HARD_BLOCK)) {
    for (const pattern of patterns) {
      if (pattern.test(command)) {
        process.stderr.write(
          `[bash-firewall] BLOCKED (${category}): This command is not permitted.\nCommand: ${command}\n`
        )
        process.exit(2)
      }
    }
  }

  for (const [category, patterns] of Object.entries(ASK)) {
    for (const pattern of patterns) {
      if (pattern.test(command)) {
        process.stdout.write(
          JSON.stringify({
            hookSpecificOutput: {
              hookEventName: 'PreToolUse',
              permissionDecision: 'ask',
              permissionDecisionReason: `[bash-firewall] Command matches sensitive category "${category}". Confirm this is intentional.\nCommand: ${command}`,
            },
          })
        )
        process.exit(0)
      }
    }
  }

  process.exit(0)
})
