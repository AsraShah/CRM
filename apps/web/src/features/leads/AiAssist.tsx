import { useState } from 'react'

import { TextArea } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { formatDateTime } from '@/lib/dates'
import { useAiStatus, useDraftDecision, useRequestDraft } from '@/lib/queries'
import type { Activity, SessionContext } from '@/lib/types'

/**
 * Optional AI assistance (CRM12).
 *
 * Shown only while the workspace has AI enabled and within budget. The draft
 * appears beside the notes it was built from so it can be checked; the person
 * edits it, copies it, or rejects it. Nothing is sent and nothing is changed.
 */
export function AiAssist({
  activities,
  session,
}: {
  activities: Activity[]
  session: SessionContext
}) {
  const status = useAiStatus(session.workspace.id)
  const request = useRequestDraft()
  const decision = useDraftDecision()
  const [text, setText] = useState('')

  if (!status.data?.enabled || activities.length === 0) return null
  if (!session.permissions.includes('ai.use')) return null

  const result = request.data
  const sources = activities.slice(0, 20)

  function ask(purpose: 'summarise_notes' | 'draft_follow_up') {
    decision.reset()
    request.mutate(
      { purpose, activityIds: sources.map((a) => a.id) },
      {
        onSuccess: ({ draft }) => {
          const out = draft.output
          setText(
            purpose === 'summarise_notes'
              ? [out.summary, out.suggested_next_action && `Next: ${out.suggested_next_action}`]
                  .filter(Boolean)
                  .join('\n\n')
              : [out.subject && `Subject: ${out.subject}`, out.body].filter(Boolean).join('\n\n'),
          )
        },
      },
    )
  }

  return (
    <fieldset className={ui.fieldset}>
      <legend>Assistance (optional)</legend>
      <div className={ui.actions}>
        <button type="button" className={ui.secondary} disabled={request.isPending} onClick={() => ask('summarise_notes')}>
          Summarise these notes
        </button>
        <button type="button" className={ui.secondary} disabled={request.isPending} onClick={() => ask('draft_follow_up')}>
          Draft a follow-up
        </button>
      </div>
      {request.isPending ? <p role="status">Drafting…</p> : null}
      <ErrorNotice error={request.error} fallback="No draft could be produced. The notes are below." />

      {result ? (
        <div className={ui.form}>
          <p className={ui.notice} style={{ margin: 0 }}>
            {result.review_note}
          </p>
          {result.draft.uncertainties.length > 0 ? (
            <div className={ui.warning}>
              <strong>The draft is unsure about:</strong>
              <ul style={{ margin: 'var(--space-1) 0 0', paddingLeft: 'var(--space-5)' }}>
                {result.draft.uncertainties.map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <label className={ui.field}>
            <span>Draft (edit before using)</span>
            <TextArea limit={LIMITS.longText} rows={6} value={text} onChange={setText} />
          </label>
          <details>
            <summary>Source notes ({result.sources.length})</summary>
            <ul className={ui.list}>
              {result.sources.map((s) => (
                <li key={s.id} style={{ fontSize: 'var(--text-sm)' }}>
                  <span className={ui.muted}>{formatDateTime(s.occurred_at)}</span>
                  <div style={{ whiteSpace: 'pre-wrap' }}>{s.outcome || '(no outcome recorded)'}</div>
                </li>
              ))}
            </ul>
          </details>
          {decision.isSuccess ? (
            <p className={ui.success} role="status">
              Recorded. It has not been sent anywhere.
            </p>
          ) : (
            <div className={ui.actions}>
              <button
                type="button"
                className={ui.primary}
                onClick={() => {
                  void navigator.clipboard?.writeText(text).catch(() => undefined)
                  decision.mutate({ draftId: result.draft.id, accepted: true })
                }}
              >
                Use it (copies the text)
              </button>
              <button
                type="button"
                className={ui.secondary}
                onClick={() => decision.mutate({ draftId: result.draft.id, accepted: false })}
              >
                Reject
              </button>
            </div>
          )}
        </div>
      ) : null}
    </fieldset>
  )
}
