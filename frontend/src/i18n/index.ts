import type { Language } from '../types'
import { common } from './dicts/common'
import { sidebar } from './dicts/sidebar'
import { composer } from './dicts/composer'
import { chat } from './dicts/chat'
import { messages } from './dicts/messages'
import { toolcalls } from './dicts/toolcalls'
import { toast } from './dicts/toast'
import { approval } from './dicts/approval'
import { settings } from './dicts/settings'
import { rules } from './dicts/rules'
import { integrations } from './dicts/integrations'

const en = {
  ...common.en,
  ...sidebar.en,
  ...composer.en,
  ...chat.en,
  ...messages.en,
  ...toolcalls.en,
  ...toast.en,
  ...approval.en,
  ...settings.en,
  ...rules.en,
  ...integrations.en,
} as const

const fa: Record<keyof typeof en, string> = {
  ...common.fa,
  ...sidebar.fa,
  ...composer.fa,
  ...chat.fa,
  ...messages.fa,
  ...toolcalls.fa,
  ...toast.fa,
  ...approval.fa,
  ...settings.fa,
  ...rules.fa,
  ...integrations.fa,
}

export type TKey = keyof typeof en
export type Dict = Readonly<Record<TKey, string>>

export const dictionaries: Record<Language, Dict> = { en, fa }

const VAR_TOKEN = /\{(\w+)\}/g

export function interpolate(
  tpl: string,
  vars?: Record<string, string | number>,
): string {
  if (!vars) return tpl
  return tpl.replace(VAR_TOKEN, (m, name) => {
    const v = vars[name]
    return v === undefined || v === null ? m : String(v)
  })
}

export function translate(
  lang: Language,
  key: TKey,
  vars?: Record<string, string | number>,
): string {
  const dict = dictionaries[lang] ?? dictionaries.en
  const tpl = dict[key] ?? dictionaries.en[key] ?? key
  return interpolate(tpl, vars)
}
