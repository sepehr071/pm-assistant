const en = {
  'messages.ruleFired': 'Rule fired · {trigger}',
  'messages.runningToolsOne': 'Running 1 tool…',
  'messages.runningToolsOther': 'Running {n} tools…',
  // Per-turn usage meta under an assistant bubble. With cost: "1.2k tok · $0.0031".
  'messages.usageTokens': '{tokens} tok',
  'messages.usageTokensCost': '{tokens} tok · {cost}',
  // Inline notice when the backend cut a turn short at the tool-step limit.
  'messages.truncatedNotice':
    'Response stopped at the tool-step limit — ask to continue.',
} as const

const fa: Record<keyof typeof en, string> = {
  'messages.ruleFired': 'قانون اجرا شد · {trigger}',
  'messages.runningToolsOne': 'در حال اجرای ۱ ابزار…',
  'messages.runningToolsOther': 'در حال اجرای {n} ابزار…',
  'messages.usageTokens': '{tokens} توکن',
  'messages.usageTokensCost': '{tokens} توکن · {cost}',
  'messages.truncatedNotice':
    'پاسخ در سقف مراحل ابزار متوقف شد — برای ادامه درخواست دهید.',
}

export const messages = { en, fa }
