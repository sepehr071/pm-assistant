import type { Language } from '../types'

const HTML_LANG: Record<Language, string> = {
  en: 'en',
  fa: 'fa',
}

const HTML_DIR: Record<Language, 'ltr' | 'rtl'> = {
  en: 'ltr',
  fa: 'rtl',
}

/**
 * Sync `<html lang>` and `<html dir>` to the active UI language.
 * Tailwind v4 reads `dir` directly for `rtl:` variants and logical
 * properties (`ps-`, `pe-`, `start-`, `end-`, `border-s-`, `rounded-e-`)
 * already swap automatically when this flips.
 */
export function applyLanguage(lang: Language): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  if (!root) return
  root.lang = HTML_LANG[lang]
  root.dir = HTML_DIR[lang]
}
