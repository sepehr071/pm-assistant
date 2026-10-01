const en = {
  'tool.statusRunning': 'running',
  'tool.statusDone': 'done',
  'tool.statusError': 'error',
  'tool.statusPending': 'pending',
  'tool.statusComplete': 'complete',
  'tool.arguments': 'Arguments',
  'tool.result': 'Result',
  'tool.countOne': '1 tool',
  'tool.countOther': '{n} tools',
  'tool.pendingCountOne': '1 pending',
  'tool.pendingCountOther': '{n} pending',
  'tool.runningCountOne': '1 running',
  'tool.runningCountOther': '{n} running',
} as const

const fa: Record<keyof typeof en, string> = {
  'tool.statusRunning': 'در حال اجرا',
  'tool.statusDone': 'انجام شد',
  'tool.statusError': 'خطا',
  'tool.statusPending': 'در انتظار',
  'tool.statusComplete': 'کامل',
  'tool.arguments': 'آرگومان‌ها',
  'tool.result': 'نتیجه',
  'tool.countOne': '۱ ابزار',
  'tool.countOther': '{n} ابزار',
  'tool.pendingCountOne': '۱ در انتظار',
  'tool.pendingCountOther': '{n} در انتظار',
  'tool.runningCountOne': '۱ در حال اجرا',
  'tool.runningCountOther': '{n} در حال اجرا',
}

export const toolcalls = { en, fa }
