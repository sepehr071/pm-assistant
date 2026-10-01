import { useState } from 'react'
import clsx from 'clsx'
import { compileRule, createRule, updateRule } from '../lib/api'
import {
  INTERVAL_OPTIONS,
  type CompiledSpec,
  type Rule,
  type RuleFilter,
} from '../types/rules'
import { useT } from '../i18n/useT'
import type { TKey } from '../i18n'

export interface RuleEditorProps {
  open: boolean
  onClose(): void
  onCreated(rule: Rule): void
  /** When set, the editor runs in edit mode: prefills from this rule and
   * saves via PATCH instead of POST. */
  rule?: Rule | null
  /** Called after a successful PATCH in edit mode. Falls back to
   * `onCreated` when omitted. */
  onSaved?(rule: Rule): void
}

type Step = 'describe' | 'review'

function FilterHuman({ filter }: { filter: RuleFilter }) {
  const { t } = useT()
  switch (filter.kind) {
    case 'message_from_user_contains':
      return (
        <div className="space-y-1 text-sm text-neutral-200">
          <div>
            <span className="text-neutral-500">
              {t('rules.editor.filter.whenUser')}
            </span>{' '}
            <span className="font-mono text-neutral-100" dir="ltr">
              {filter.user}
            </span>{' '}
            <span className="text-neutral-500">
              {t('rules.editor.filter.sendsMessageContaining')}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(filter.contains ?? []).map((k) => (
              <span
                key={k}
                dir="ltr"
                className="rounded-md border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-xs text-neutral-100"
              >
                {k}
              </span>
            ))}
          </div>
        </div>
      )
    case 'new_issue_mentions':
      return (
        <div className="text-sm text-neutral-200">
          <span className="text-neutral-500">
            {t('rules.editor.filter.whenNewIssueMentions')}
          </span>{' '}
          <span className="font-mono text-neutral-100" dir="ltr">
            {filter.mention}
          </span>
          {filter.repo && (
            <>
              {' '}
              <span className="text-neutral-500">in</span>{' '}
              <span className="font-mono text-neutral-100" dir="ltr">
                {filter.repo}
              </span>
            </>
          )}
        </div>
      )
    case 'new_pr_review_request':
      return (
        <div className="text-sm text-neutral-200">
          <span className="text-neutral-500">
            {t('rules.editor.filter.whenNewPrReviewRequest')}
          </span>
          {filter.mention && (
            <>
              {' '}
              <span className="text-neutral-500">
                {t('rules.editor.filter.from')}
              </span>{' '}
              <span className="font-mono text-neutral-100" dir="ltr">
                {filter.mention}
              </span>
            </>
          )}
          {filter.repo && (
            <>
              {' '}
              <span className="text-neutral-500">in</span>{' '}
              <span className="font-mono text-neutral-100" dir="ltr">
                {filter.repo}
              </span>
            </>
          )}
        </div>
      )
    case 'schedule_only':
      return (
        <div className="text-sm text-neutral-200">
          <span className="text-neutral-500">
            {t('rules.editor.filter.scheduleOnly')}
          </span>
        </div>
      )
    default: {
      const exhaustive: never = filter
      void exhaustive
      return (
        <div className="text-sm text-neutral-400">
          {t('rules.editor.filter.unknown')}
        </div>
      )
    }
  }
}

function SpecPreview({ spec }: { spec: CompiledSpec }) {
  const { t } = useT()
  const argsText = (() => {
    try {
      return JSON.stringify(spec.source_args ?? {}, null, 2)
    } catch {
      return String(spec.source_args ?? '')
    }
  })()

  return (
    <div className="space-y-4" data-testid="rule-editor-review">
      <section>
        <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">
          {t('rules.editor.checks')}
        </div>
        <div className="glass-pre rounded-md p-3 space-y-2">
          <div className="text-sm text-neutral-200">
            <span className="text-neutral-500">{t('rules.editor.tool')}</span>{' '}
            <span className="font-mono text-neutral-100" dir="ltr">
              {spec.source_tool}
            </span>
          </div>
          <pre
            dir="ltr"
            className="overflow-auto whitespace-pre-wrap break-all text-xs text-neutral-300"
          >
            {argsText}
          </pre>
        </div>
      </section>

      <section>
        <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">
          {t('rules.editor.matchesWhen')}
        </div>
        <div className="glass-pre rounded-md p-3">
          <FilterHuman filter={spec.filter} />
        </div>
      </section>

      <section>
        <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">
          {t('rules.editor.action')}
        </div>
        <div className="glass-pre rounded-md p-3 text-sm text-neutral-100 whitespace-pre-wrap">
          {spec.action_prompt}
        </div>
      </section>
    </div>
  )
}

/**
 * Thin gate. Returns `null` while closed and remounts the stateful body
 * (keyed by target rule id) whenever the editor opens against a different
 * rule, so prefill happens via `useState` initializers — no effect, no
 * cascading-render lint trap.
 */
export function RuleEditor(props: RuleEditorProps) {
  if (!props.open) return null
  return <RuleEditorInner key={props.rule?.id ?? 'new'} {...props} />
}

function RuleEditorInner({
  onClose,
  onCreated,
  rule,
  onSaved,
}: RuleEditorProps) {
  const { t } = useT()
  const isEdit = !!rule
  const [step, setStep] = useState<Step>('describe')
  const [description, setDescription] = useState(rule?.description ?? '')
  const originalDescription = rule?.description ?? ''
  const [intervalSeconds, setIntervalSeconds] = useState(
    rule?.interval_seconds ?? 300,
  )
  const [autoApprove, setAutoApprove] = useState(rule?.auto_approve ?? false)
  const [compiled, setCompiled] = useState<CompiledSpec | null>(
    rule?.compiled_spec ?? null,
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Has the user changed the wording since opening an existing rule? When
  // true we must recompile before saving so the spec stays in sync.
  const descriptionChanged =
    description.trim() !== originalDescription.trim()

  const resetAndClose = () => {
    onClose()
  }

  const handleCompile = async () => {
    if (busy) return
    if (!description.trim()) {
      setError(t('rules.editor.descRequired'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      // Step 1 only previews the compiled spec — nothing is persisted
      // until the user clicks Save in step 2.
      const resp = await compileRule({ description: description.trim() })
      setCompiled(resp.compiled_spec)
      setStep('review')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // Edit mode: jump to the review step using the spec we already have, no
  // recompile (used when the description is untouched).
  const handleReviewExisting = () => {
    if (!compiled) return
    setError(null)
    setStep('review')
  }

  const handleSave = async () => {
    if (busy || !compiled) return
    setBusy(true)
    setError(null)
    try {
      if (isEdit && rule) {
        const saved = await updateRule(rule.id, {
          description: description.trim(),
          interval_seconds: intervalSeconds,
          auto_approve: autoApprove,
          compiled_spec: compiled,
        })
        ;(onSaved ?? onCreated)(saved)
      } else {
        const created = await createRule({
          description: description.trim(),
          interval_seconds: intervalSeconds,
          auto_approve: autoApprove,
          compiled_spec: compiled,
        })
        onCreated(created)
      }
      resetAndClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const titleKey: TKey =
    step === 'review'
      ? 'rules.editor.titleReview'
      : isEdit
        ? 'rules.editor.titleEdit'
        : 'rules.editor.titleNew'
  const subtitleKey: TKey =
    step === 'review'
      ? 'rules.editor.reviewStep'
      : isEdit
        ? 'rules.editor.editStep'
        : 'rules.editor.descStep'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="rule-editor-title"
      data-testid="rule-editor"
      className="glass-backdrop fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) resetAndClose()
      }}
    >
      <div className="glass-elevated w-full max-w-2xl rounded-2xl overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-3">
          <div>
            <h2
              id="rule-editor-title"
              className="text-base font-semibold text-neutral-100"
            >
              {t(titleKey)}
            </h2>
            <p className="mt-0.5 text-xs text-neutral-500">{t(subtitleKey)}</p>
          </div>
          <div className="flex items-center gap-1 text-xs">
            <span
              className={clsx(
                'rounded-full px-2 py-0.5 border',
                step === 'describe'
                  ? 'border-blue-400/40 bg-blue-500/20 text-blue-100'
                  : 'border-white/10 bg-white/5 text-neutral-400'
              )}
            >
              {t('rules.editor.step1')}
            </span>
            <span className="text-neutral-600">·</span>
            <span
              className={clsx(
                'rounded-full px-2 py-0.5 border',
                step === 'review'
                  ? 'border-blue-400/40 bg-blue-500/20 text-blue-100'
                  : 'border-white/10 bg-white/5 text-neutral-400'
              )}
            >
              {t('rules.editor.step2')}
            </span>
          </div>
        </div>

        {step === 'describe' && (
          <div className="px-5 py-4 space-y-4">
            <div>
              <label
                htmlFor="rule-description"
                className="mb-1 block text-sm text-neutral-300"
              >
                {t('rules.editor.descLabel')}
              </label>
              <textarea
                id="rule-description"
                data-testid="rule-editor-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                placeholder={t('rules.editor.descPlaceholder')}
                className="glass-input w-full rounded-lg px-3 py-2 text-sm text-neutral-100 outline-none"
              />
              <p className="mt-1 text-xs text-neutral-500">
                {t('rules.editor.descHelp')}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label
                  htmlFor="rule-interval"
                  className="mb-1 block text-sm text-neutral-300"
                >
                  {t('rules.editor.checkEvery')}
                </label>
                <select
                  id="rule-interval"
                  data-testid="rule-editor-interval"
                  value={intervalSeconds}
                  onChange={(e) => setIntervalSeconds(Number(e.target.value))}
                  className="glass-input w-full rounded-lg px-3 py-2 text-sm text-neutral-100 outline-none"
                >
                  {INTERVAL_OPTIONS.map((o) => (
                    <option
                      key={o.seconds}
                      value={o.seconds}
                      className="bg-neutral-900 text-neutral-100"
                    >
                      {t(o.labelKey as TKey)}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex flex-col">
                <span className="mb-1 block text-sm text-neutral-300">
                  {t('rules.editor.autoApprove')}
                </span>
                <label
                  className={clsx(
                    'flex items-center gap-2 rounded-lg border px-3 py-2 text-sm cursor-pointer transition',
                    autoApprove
                      ? 'border-red-500/40 bg-red-500/10 text-red-100'
                      : 'glass-input text-neutral-200'
                  )}
                >
                  <input
                    type="checkbox"
                    data-testid="rule-editor-auto-approve"
                    checked={autoApprove}
                    onChange={(e) => setAutoApprove(e.target.checked)}
                    className="accent-red-500"
                  />
                  <span>{t('rules.editor.fireWithoutAsking')}</span>
                </label>
              </div>
            </div>

            {autoApprove && (
              <div className="rounded-lg border border-red-500/40 bg-red-950/30 px-3 py-2 text-xs text-red-200">
                {t('rules.editor.autoApproveWarning')}
              </div>
            )}

            {error && (
              <div className="text-xs text-red-400" data-testid="rule-editor-error">
                {error}
              </div>
            )}
          </div>
        )}

        {step === 'review' && compiled && (
          <div className="px-5 py-4 space-y-4">
            <SpecPreview spec={compiled} />
            <div className="grid grid-cols-2 gap-3 border-t border-white/10 pt-3">
              <div className="text-xs text-neutral-500">
                {t('rules.editor.intervalSummary')}
                <div className="mt-0.5 text-sm text-neutral-100">
                  {(() => {
                    const opt = INTERVAL_OPTIONS.find(
                      (o) => o.seconds === intervalSeconds,
                    )
                    return opt
                      ? t(opt.labelKey as TKey)
                      : t('rules.interval.fmt.seconds', { n: intervalSeconds })
                  })()}
                </div>
              </div>
              <div className="text-xs text-neutral-500">
                {t('rules.editor.autoApproveSummary')}
                <div className="mt-0.5 text-sm text-neutral-100">
                  {autoApprove
                    ? t('rules.editor.autoApproveYes')
                    : t('rules.editor.autoApproveNo')}
                </div>
              </div>
            </div>
            {error && (
              <div className="text-xs text-red-400" data-testid="rule-editor-error">
                {error}
              </div>
            )}
          </div>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-white/10 px-5 py-3">
          <button
            type="button"
            onClick={() => {
              if (step === 'review') {
                setStep('describe')
              } else {
                resetAndClose()
              }
            }}
            disabled={busy}
            data-testid="rule-editor-back"
            className="rounded-lg border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-neutral-200 backdrop-blur hover:bg-white/10 transition disabled:opacity-50"
          >
            {step === 'review'
              ? t('rules.editor.back')
              : t('rules.editor.cancel')}
          </button>

          {step === 'describe' ? (
            <div className="flex items-center gap-2">
              {isEdit && !descriptionChanged && compiled && (
                <button
                  type="button"
                  onClick={handleReviewExisting}
                  disabled={busy}
                  data-testid="rule-editor-review-step"
                  className="rounded-lg border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-neutral-200 backdrop-blur hover:bg-white/10 transition disabled:opacity-50"
                >
                  {t('rules.editor.reviewExisting')}
                </button>
              )}
              <button
                type="button"
                onClick={() => void handleCompile()}
                disabled={busy || !description.trim()}
                data-testid="rule-editor-compile"
                className="rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-4 py-1.5 text-sm font-medium text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500 transition disabled:opacity-50"
              >
                {busy
                  ? t('rules.editor.compiling')
                  : t('rules.editor.compile')}
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={busy}
              data-testid="rule-editor-save"
              className="rounded-lg bg-gradient-to-br from-emerald-500 to-emerald-600 px-4 py-1.5 text-sm font-medium text-white shadow-lg shadow-emerald-500/30 ring-1 ring-emerald-300/20 hover:from-emerald-400 hover:to-emerald-500 transition disabled:opacity-50"
            >
              {isEdit
                ? t('rules.editor.saveChanges')
                : t('rules.editor.save')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export default RuleEditor
