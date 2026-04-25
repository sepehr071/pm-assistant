import { useState } from 'react'
import clsx from 'clsx'
import { createRule } from '../lib/api'
import {
  INTERVAL_OPTIONS,
  type CompiledSpec,
  type Rule,
  type RuleFilter,
} from '../types/rules'

export interface RuleEditorProps {
  open: boolean
  onClose(): void
  onCreated(rule: Rule): void
}

type Step = 'describe' | 'review'

function filterHuman(filter: RuleFilter): React.ReactNode {
  switch (filter.kind) {
    case 'message_from_user_contains':
      return (
        <div className="space-y-1 text-sm text-neutral-200">
          <div>
            <span className="text-neutral-500">When user</span>{' '}
            <span className="font-mono text-neutral-100">{filter.user}</span>{' '}
            <span className="text-neutral-500">sends a message containing</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(filter.contains ?? []).map((k) => (
              <span
                key={k}
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
          <span className="text-neutral-500">When a new issue mentions</span>{' '}
          {(filter.mentions ?? []).map((m, i) => (
            <span key={m}>
              {i > 0 && <span className="text-neutral-500">, </span>}
              <span className="font-mono text-neutral-100">{m}</span>
            </span>
          ))}
        </div>
      )
    case 'new_pr_review_request':
      return (
        <div className="text-sm text-neutral-200">
          <span className="text-neutral-500">
            When a new PR review is requested
          </span>
          {filter.reviewer && (
            <>
              {' '}
              <span className="text-neutral-500">from</span>{' '}
              <span className="font-mono text-neutral-100">
                {filter.reviewer}
              </span>
            </>
          )}
        </div>
      )
    case 'schedule_only':
      return (
        <div className="text-sm text-neutral-200">
          <span className="text-neutral-500">
            On every scheduled tick (no other condition)
          </span>
        </div>
      )
    default: {
      const exhaustive: never = filter
      void exhaustive
      return (
        <div className="text-sm text-neutral-400">Unknown filter</div>
      )
    }
  }
}

function SpecPreview({ spec }: { spec: CompiledSpec }) {
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
          Checks
        </div>
        <div className="glass-pre rounded-md p-3 space-y-2">
          <div className="text-sm text-neutral-200">
            <span className="text-neutral-500">Tool</span>{' '}
            <span className="font-mono text-neutral-100">
              {spec.source_tool}
            </span>
          </div>
          <pre className="overflow-auto whitespace-pre-wrap break-all text-xs text-neutral-300">
            {argsText}
          </pre>
        </div>
      </section>

      <section>
        <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">
          Matches when
        </div>
        <div className="glass-pre rounded-md p-3">{filterHuman(spec.filter)}</div>
      </section>

      <section>
        <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">
          Action
        </div>
        <div className="glass-pre rounded-md p-3 text-sm text-neutral-100 whitespace-pre-wrap">
          {spec.action_prompt}
        </div>
      </section>
    </div>
  )
}

export function RuleEditor({ open, onClose, onCreated }: RuleEditorProps) {
  const [step, setStep] = useState<Step>('describe')
  const [description, setDescription] = useState('')
  const [intervalSeconds, setIntervalSeconds] = useState(300)
  const [autoApprove, setAutoApprove] = useState(false)
  const [compiled, setCompiled] = useState<CompiledSpec | null>(null)
  const [draft, setDraft] = useState<Rule | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!open) return null

  const resetAndClose = () => {
    setStep('describe')
    setDescription('')
    setIntervalSeconds(300)
    setAutoApprove(false)
    setCompiled(null)
    setDraft(null)
    setBusy(false)
    setError(null)
    onClose()
  }

  const handleCompile = async () => {
    if (busy) return
    if (!description.trim()) {
      setError('Please describe the rule first.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const rule = await createRule({
        description: description.trim(),
        interval_seconds: intervalSeconds,
        auto_approve: autoApprove,
      })
      setDraft(rule)
      setCompiled(rule.compiled_spec)
      setStep('review')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const handleSave = () => {
    if (!draft) return
    onCreated(draft)
    resetAndClose()
  }

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
              {step === 'describe' ? 'New rule' : 'Review compiled rule'}
            </h2>
            <p className="mt-0.5 text-xs text-neutral-500">
              {step === 'describe'
                ? 'Describe what should trigger the rule and what action to take.'
                : 'The assistant compiled your description into a structured spec. Review it before saving.'}
            </p>
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
              1. Describe
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
              2. Review
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
                Description
              </label>
              <textarea
                id="rule-description"
                data-testid="rule-editor-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                placeholder={
                  'e.g. Whenever Sepehr DMs me on Slack about the roadmap, reply that I will check today.'
                }
                className="glass-input w-full rounded-lg px-3 py-2 text-sm text-neutral-100 outline-none"
              />
              <p className="mt-1 text-xs text-neutral-500">
                Plain English. The assistant will turn this into a structured
                spec you can review before saving.
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label
                  htmlFor="rule-interval"
                  className="mb-1 block text-sm text-neutral-300"
                >
                  Check every
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
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex flex-col">
                <span className="mb-1 block text-sm text-neutral-300">
                  Auto-approve writes
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
                  <span>Fire without asking</span>
                </label>
              </div>
            </div>

            {autoApprove && (
              <div className="rounded-lg border border-red-500/40 bg-red-950/30 px-3 py-2 text-xs text-red-200">
                This rule will run write tool calls (send messages, create
                issues, etc.) with no approval popup. Use only for low-stakes
                responses.
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
                Interval
                <div className="mt-0.5 text-sm text-neutral-100">
                  {INTERVAL_OPTIONS.find((o) => o.seconds === intervalSeconds)
                    ?.label ?? `${intervalSeconds}s`}
                </div>
              </div>
              <div className="text-xs text-neutral-500">
                Auto-approve
                <div className="mt-0.5 text-sm text-neutral-100">
                  {autoApprove ? 'Yes — writes fire silently' : 'No — writes wait for approval'}
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
            {step === 'review' ? 'Back' : 'Cancel'}
          </button>

          {step === 'describe' ? (
            <button
              type="button"
              onClick={() => void handleCompile()}
              disabled={busy || !description.trim()}
              data-testid="rule-editor-compile"
              className="rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-4 py-1.5 text-sm font-medium text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500 transition disabled:opacity-50"
            >
              {busy ? 'Compiling…' : 'Compile & preview'}
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSave}
              disabled={busy}
              data-testid="rule-editor-save"
              className="rounded-lg bg-gradient-to-br from-emerald-500 to-emerald-600 px-4 py-1.5 text-sm font-medium text-white shadow-lg shadow-emerald-500/30 ring-1 ring-emerald-300/20 hover:from-emerald-400 hover:to-emerald-500 transition disabled:opacity-50"
            >
              Save rule
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export default RuleEditor
