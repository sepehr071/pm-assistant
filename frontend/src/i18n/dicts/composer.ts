const en = {
  'composer.placeholder': 'Send a message…',
  'composer.send': 'Send',
} as const

const fa: Record<keyof typeof en, string> = {
  'composer.placeholder': 'پیام بنویسید…',
  'composer.send': 'ارسال',
}

export const composer = { en, fa }
