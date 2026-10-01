import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listRuleHealth } from '../lib/api'
import RuleEditor from '../components/RuleEditor'
import RuleHealthCard from '../components/rules/RuleHealthCard'
import type { Rule } from '../types/rules'
import { useT } from '../i18n/useT'

export default function Rules() {
  const qc = useQueryClient()
  const { t } = useT()
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<Rule | null>(null)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['rule-health'],
    queryFn: listRuleHealth,
    refetchInterval: 30_000,
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['rule-health'] })
    qc.invalidateQueries({ queryKey: ['rules'] })
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-8 space-y-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-neutral-100">
              {t('rules.title')}
            </h1>
            <p className="text-sm text-neutral-500">{t('rules.subtitle')}</p>
          </div>
          <button
            type="button"
            onClick={() => {
              setEditingRule(null)
              setEditorOpen(true)
            }}
            data-testid="rules-new"
            className="rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500 transition"
          >
            {t('rules.newRule')}
          </button>
        </div>

        {isLoading && (
          <div
            className="space-y-2"
            data-testid="rules-loading"
            aria-busy="true"
          >
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="glass-panel h-40 animate-pulse rounded-2xl"
              />
            ))}
          </div>
        )}

        {isError && (
          <div className="glass-panel rounded-2xl p-5 text-sm text-red-400">
            {t('rules.failedToLoad', { error: (error as Error).message })}
          </div>
        )}

        {!isLoading && !isError && (data?.length ?? 0) === 0 && (
          <div
            className="glass-panel rounded-2xl p-8 text-center"
            data-testid="rules-empty"
          >
            <div className="text-sm text-neutral-300">{t('rules.empty')}</div>
            <p className="mt-1 text-xs text-neutral-500">
              {t('rules.emptyHelp')}
            </p>
            <button
              type="button"
              onClick={() => {
                setEditingRule(null)
                setEditorOpen(true)
              }}
              className="mt-4 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-4 py-1.5 text-sm font-medium text-white shadow-lg shadow-blue-500/25 ring-1 ring-blue-300/20 hover:from-blue-400 hover:to-blue-500 transition"
            >
              {t('rules.createFirst')}
            </button>
          </div>
        )}

        {!isLoading && !isError && (data?.length ?? 0) > 0 && (
          <ul className="space-y-3" data-testid="rules-list">
            {data!.map((h) => (
              <li key={h.rule.id} data-testid={`rule-row-${h.rule.id}`}>
                <RuleHealthCard
                  health={h}
                  onEdit={(rule) => {
                    setEditingRule(rule)
                    setEditorOpen(true)
                  }}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      <RuleEditor
        open={editorOpen}
        rule={editingRule}
        onClose={() => {
          setEditorOpen(false)
          setEditingRule(null)
        }}
        onCreated={() => {
          invalidate()
        }}
        onSaved={() => {
          invalidate()
        }}
      />
    </div>
  )
}
