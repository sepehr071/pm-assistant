/**
 * Each area dict file under ./dicts/ exports `{ en, fa }`.
 *
 * - `en` is declared with `as const` so its keys flow into the global
 *   `TKey` type derived in ../index.ts.
 * - `fa` is typed as `Record<keyof typeof en, string>` so TypeScript
 *   refuses to compile when a Persian translation is missing or stray.
 *
 * Keys are flat and namespace-prefixed (`sidebar.newChat`,
 * `settings.title`, `rules.empty`) so we can ship them as one object per
 * language. Two-tier nesting was rejected as overkill for ~150 strings.
 */
export type AreaEn = Readonly<Record<string, string>>
