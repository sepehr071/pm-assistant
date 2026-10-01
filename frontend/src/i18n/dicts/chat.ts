const en = {
  'chat.empty': 'Select or create a chat.',
  'chat.emptyTitle': 'What should we move forward today?',
  'chat.emptyHint':
    'Ask across your connected tools. Reads run right away; anything that writes waits for your approval.',
  'chat.example1': 'Summarize sprint progress from Jira and list blocked issues',
  'chat.example2': 'Which pull requests are waiting on my review?',
  'chat.example3': 'Draft a status update for #standup in Slack',
  'chat.loadingMessages': 'Loading messages…',
  'chat.failedToLoad': 'Failed to load: {error}',
  'chat.streamError': 'Stream error: {error}',
  'chat.streamEndedUnexpectedly': 'Stream ended unexpectedly.',
  'chat.readOnlyRuleActivity':
    'This is a read-only rule-activity log. Edit the rule to change behaviour.',
  // Per-chat running token total near the header. Subtle, non-shouting.
  'chat.usageTotal': '{tokens} tok this session',
  'chat.usageTotalCost': '{tokens} tok · {cost} this session',
} as const

const fa: Record<keyof typeof en, string> = {
  'chat.empty': 'یک گفتگو را انتخاب کنید یا یکی بسازید.',
  'chat.emptyTitle': 'امروز چه کاری را جلو ببریم؟',
  'chat.emptyHint':
    'از ابزارهای متصل\u200cتان بپرسید. خواندن\u200cها فوراً اجرا می\u200cشوند؛ هر کاری که چیزی را تغییر دهد منتظر تأیید شما می\u200cماند.',
  'chat.example1': 'پیشرفت اسپرینت را از Jira خلاصه کن و تسک\u200cهای بلاک را فهرست کن',
  'chat.example2': 'کدام PRها منتظر ریویوی من هستند؟',
  'chat.example3': 'یک گزارش وضعیت برای کانال #standup در Slack بنویس',
  'chat.loadingMessages': 'در حال بارگذاری پیام‌ها…',
  'chat.failedToLoad': 'بارگذاری ناموفق بود: {error}',
  'chat.streamError': 'خطای استریم: {error}',
  'chat.streamEndedUnexpectedly': 'استریم به‌طور غیرمنتظره پایان یافت.',
  'chat.readOnlyRuleActivity':
    'این یک گزارش فعالیت قوانین فقط-خواندنی است. برای تغییر رفتار، قانون را ویرایش کنید.',
  'chat.usageTotal': '{tokens} توکن در این نشست',
  'chat.usageTotalCost': '{tokens} توکن · {cost} در این نشست',
}

export const chat = { en, fa }
