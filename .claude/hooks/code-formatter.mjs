#!/usr/bin/env node
import { createInterface } from 'readline'
import { execSync } from 'child_process'
import path from 'path'

const rl = createInterface({ input: process.stdin })
let raw = ''
rl.on('line', line => (raw += line))
rl.on('close', () => {
  const input = JSON.parse(raw || '{}')
  const filePath = input.tool_input?.file_path || ''
  const ext = path.extname(filePath).toLowerCase()

  if (ext !== '.py') process.exit(0)

  try {
    execSync(`black "${filePath}"`, {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'],
    })
  } catch (err) {
    process.stdout.write(`[code-formatter] black warning on ${filePath}: ${err.message}\n`)
  }

  process.exit(0)
})
