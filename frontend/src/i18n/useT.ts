import { useCallback } from 'react'
import { useAppStore } from '../store'
import { translate, type TKey } from './index'
import type { Language } from '../types'

/**
 * Translation hook. Reads the active language out of the settings store
 * (defaults to "fa" before the first /api/settings hydration completes
 * so first paint matches the html dir/lang in index.html).
 *
 * Returns:
 * - `t(key, vars?)` — looks up the string and interpolates `{name}`
 *   tokens. Falls back to English then to the key itself.
 * - `lang` — current language; useful for conditionals and pluralization.
 */
export function useT() {
  const lang = useAppStore((s) => (s.settings?.language ?? 'fa') as Language)
  const t = useCallback(
    (key: TKey, vars?: Record<string, string | number>) =>
      translate(lang, key, vars),
    [lang],
  )
  return { t, lang }
}
