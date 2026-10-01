// Capture README screenshots from a running demo instance.
//
//   1. Seed + start the backend on the demo DB (see scripts/seed_demo.py).
//   2. Start the frontend (vite) pointing at it.
//   3. node scripts/capture-screenshots.cjs [baseUrl] [outDir]
//
// Needs `playwright` resolvable (e.g. NODE_PATH pointing at a folder where it
// is installed). The approval card is driven by a mocked SSE turn — no LLM.
const { chromium } = require('playwright')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:4120'
const OUT = process.argv[3] || path.join(__dirname, '..', 'docs', 'images')

// A synthetic agent turn: one read tool that auto-runs, then a write tool
// that pauses for approval. Mirrors the event names in src/lib/stream.ts.
const SSE = [
  ['token', { delta: 'باشه، اول لیست تسک‌های بلاک را از Jira می‌گیرم.' }],
  ['tool_call_start', { tool_call_id: 'd1', tool_name: 'jira__search_issues', arguments: { jql: 'project = ACME AND status = Blocked' } }],
  ['tool_call_result', { tool_call_id: 'd1', tool_name: 'jira__search_issues', result: '{"total":2}', is_error: false }],
  ['tool_call_request', {
    tool_call_id: 'd2',
    tool_name: 'jira__transition_issue',
    arguments: { issue: 'ACME-431', transition: 'In Progress', assignee: 'bob', comment: 'Unblocked after SSO fix — picking this up.' },
  }],
]
  .map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`)
  .join('')

// Keep the mocked SSE response open (like a real turn paused on approval),
// so the UI does not report "stream ended unexpectedly".
function holdOpenStream(body) {
  const realFetch = window.fetch
  window.fetch = (url, init) => {
    if (init && init.method === 'POST' && String(url).endsWith('/messages')) {
      const stream = new ReadableStream({
        start(c) {
          c.enqueue(new TextEncoder().encode(body))
        },
      })
      return Promise.resolve(
        new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }),
      )
    }
    return realFetch(url, init)
  }
}

// Serve /api/settings with a different UI language without touching the DB.
async function forceLanguage(page, language) {
  await page.route('**/api/settings', async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const res = await route.fetch()
    const json = await res.json()
    await route.fulfill({ response: res, json: { ...json, language } })
  })
}

async function shoot(browser, name, url, opts = {}) {
  const ctx = await browser.newContext({
    viewport: opts.viewport || { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: 'dark',
  })
  const page = await ctx.newPage()
  if (opts.setup) await opts.setup(page)
  await page.goto(BASE + url, { waitUntil: 'networkidle' })
  await page.waitForSelector('main h1, main h2, [data-testid="message-list"]')
  await page.waitForTimeout(800)
  if (opts.before) await opts.before(page)
  await page.waitForTimeout(opts.settle ?? 700)
  const file = path.join(OUT, name)
  await page.screenshot({ path: file })
  console.log('saved', file)
  await ctx.close()
}

;(async () => {
  const browser = await chromium.launch()
  const chats = await (await fetch(BASE + '/api/chats')).json()
  const main = chats.find((c) => c.kind === 'user' && c.title.includes('۱۴')) || chats.find((c) => c.kind === 'user')
  const rules = await (await fetch(BASE + '/api/rules')).json()
  const rule = rules.find((r) => r.auto_approve) || rules[0]
  const scrollToEnd = (p) => p.evaluate(() => {
    const el = document.querySelector('[data-testid="message-list"]')
    if (el) el.scrollTop = el.scrollHeight
  })

  await shoot(browser, 'chat-tool-calls.png', `/chat/${main.id}`, { before: scrollToEnd })
  await shoot(browser, 'approval-gate.png', `/chat/${main.id}`, {
    setup: (p) => p.addInitScript(holdOpenStream, SSE),
    before: async (p) => {
      await p.locator('textarea').fill('تسک ACME-431 رو ببر In Progress و بده به Bob.')
      await p.keyboard.press('Enter')
    },
    settle: 1200,
  })
  await shoot(browser, 'rules-en.png', '/rules', { setup: (p) => forceLanguage(p, 'en') })
  await shoot(browser, 'rule-activity.png', `/rules/${rule.id}/activity`)
  await shoot(browser, 'settings-glass.png', '/settings', { setup: (p) => forceLanguage(p, 'en') })
  await shoot(browser, 'mobile-chat.png', `/chat/${main.id}`, {
    viewport: { width: 390, height: 844 },
    // Start at the summary message so the shot does not open mid-table.
    before: (p) => p.evaluate(() => {
      const t = document.querySelector('[data-testid="message-list"] table')
      if (t) t.parentElement.parentElement.scrollIntoView({ block: 'start' })
    }),
  })
  await shoot(browser, 'mobile-menu.png', `/chat/${main.id}`, {
    viewport: { width: 390, height: 844 },
    before: (p) => p.getByRole('button', { name: 'باز کردن منو' }).click(),
  })
  await browser.close()
})()
