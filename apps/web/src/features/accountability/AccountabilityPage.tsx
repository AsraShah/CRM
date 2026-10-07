import { CheckCircle2, Info } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext } from 'react-router'

import { TextArea } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { EmptyState } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { formatDateTime } from '@/lib/dates'
import { useExceptionResponse, useExceptions } from '@/lib/queries'
import type { ExceptionState, SessionContext, WorkException } from '@/lib/types'

const KIND_LABELS: Record<string, string> = {
  missed_deadline: 'Missed deadline',
  repeated_reschedule: 'Repeated rescheduling',
  neglected_lead: 'Lead with no owner or no contact',
  no_next_action: 'Open deal with no next action',
  handover_incomplete: 'Handover missing information',
  delivery_blocked: 'Milestone blocked or overdue',
  ticket_reopened: 'Ticket reopened after resolution',
}

const STATE_LABELS: Record<ExceptionState, string> = {
  open: 'Awaiting a response',
  under_review: 'Explained, awaiting a manager',
  disputed: 'Disputed, awaiting a manager',
  resolved: 'Resolved',
  dismissed: 'Dismissed',
}

const CLOSED: ExceptionState[] = ['resolved', 'dismissed']

/**
 * The accountability queue (CRM10).
 *
 * Wording matters more here than anywhere else in the product. Each item is a
 * specific record in a specific state — a prompt for a conversation, never a
 * verdict. So the screen shows the observable facts first, gives the person
 * concerned their own say (including an explicit dispute), and closes an item
 * only with a manager's written decision. There are no scores and no automatic
 * consequences, and nothing here implies otherwise.
 */
export function AccountabilityPage({ embedded = false }: { embedded?: boolean } = {}) {
  const session = useOutletContext<SessionContext>()
  const isManager = session.permissions.includes('exception.review')
  const [showClosed, setShowClosed] = useState(false)
  const { data, isLoading, isError } = useExceptions(session.workspace.id)

  if (isLoading) {
    return (
      <p role="status" aria-live="polite">
        Loading…
      </p>
    )
  }
  if (isError || !data) {
    return <p role="alert">The accountability queue could not be loaded.</p>
  }

  const items = showClosed
    ? data.results
    : data.results.filter((item) => !CLOSED.includes(item.state))

  return (
    <div className={ui.page}>
      <header className={ui.header}>
        <div>
          {embedded ? (
            <h2 className={ui.sectionTitle}>Accountability</h2>
          ) : (
            <h1>Accountability</h1>
          )}
          <p className={ui.subtitle}>
            {isManager
              ? 'Records that need a conversation. Each closes only with your written decision.'
              : 'Records about your work that need a response. You can explain, or dispute the facts.'}
          </p>
        </div>
        <label className={ui.toggle}>
          <input
            type="checkbox"
            checked={showClosed}
            onChange={(event) => setShowClosed(event.target.checked)}
          />
          <span>Include resolved and dismissed</span>
        </label>
      </header>

      <p className={ui.notice} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start' }}>
        <Info size={16} aria-hidden="true" style={{ flex: 'none', marginTop: 2 }} />
        These are prompts to look at specific records, not judgements about
        anybody. The system takes no action on its own.
      </p>

      {items.length === 0 ? (
        <div className={ui.card}>
          <EmptyState icon={<CheckCircle2 size={22} aria-hidden="true" />} title="Nothing needs a response">
            When a rule flags a record that needs a conversation, it appears here.
          </EmptyState>
        </div>
      ) : (
        <ul className={ui.list}>
          {items.map((item) => (
            <ExceptionCard
              key={item.id}
              item={item}
              session={session}
              isManager={isManager}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function ExceptionCard({
  item,
  session,
  isManager,
}: {
  item: WorkException
  session: SessionContext
  isManager: boolean
}) {
  const [mode, setMode] = useState<'none' | 'explain' | 'review'>('none')
  const isClosed = CLOSED.includes(item.state)
  const isMine = item.subject === session.user_id
  const canExplain = !isClosed && (isMine || isManager)
  const canReview = !isClosed && isManager

  return (
    <li className={ui.card}>
      <div className={ui.cardHeader}>
        <h3>{KIND_LABELS[item.kind] ?? item.kind}</h3>
        <span
          className={
            item.state === 'disputed'
              ? ui.pillWarning
              : isClosed
                ? ui.pillSuccess
                : ui.pillInfo
          }
        >
          {STATE_LABELS[item.state]}
        </span>
      </div>
      <p className={ui.subtitle}>
        {item.subject_email ? `Concerns ${isMine ? 'you' : item.subject_email}` : 'No individual named'}
        {' · raised '}
        {formatDateTime(item.created_at)}
      </p>

      <p style={{ margin: 'var(--space-3) 0 0' }}>{item.summary}</p>

      <Facts detail={item.detail} />

      {item.employee_explanation ? (
        <div className={ui.card} style={{ background: 'var(--colour-surface-sunken)' }}>
          <strong>
            {item.state === 'disputed' ? 'Disputed' : 'Explanation'}
            {item.employee_responded_at
              ? ` · ${formatDateTime(item.employee_responded_at)}`
              : ''}
          </strong>
          <p style={{ margin: 'var(--space-1) 0 0', whiteSpace: 'pre-wrap' }}>
            {item.employee_explanation}
          </p>
        </div>
      ) : null}

      {item.review_decision ? (
        <div className={ui.success} style={{ marginTop: 'var(--space-3)' }}>
          <strong>
            Manager decision ({item.state === 'dismissed' ? 'dismissed' : 'resolved'})
            {item.reviewed_at ? ` · ${formatDateTime(item.reviewed_at)}` : ''}
          </strong>
          <p style={{ margin: 'var(--space-1) 0 0', whiteSpace: 'pre-wrap' }}>
            {item.review_decision}
          </p>
        </div>
      ) : null}

      {mode === 'none' && (canExplain || canReview) ? (
        <div className={ui.actions} style={{ marginTop: 'var(--space-3)' }}>
          {canExplain ? (
            <button
              type="button"
              className={isMine ? ui.primary : ui.secondary}
              onClick={() => setMode('explain')}
            >
              {item.employee_explanation ? 'Update response' : 'Respond'}
            </button>
          ) : null}
          {canReview ? (
            <button
              type="button"
              className={isMine ? ui.secondary : ui.primary}
              onClick={() => setMode('review')}
            >
              Record decision
            </button>
          ) : null}
        </div>
      ) : null}

      {mode === 'explain' ? (
        <ExplainForm item={item} session={session} onDone={() => setMode('none')} />
      ) : null}
      {mode === 'review' ? (
        <ReviewForm item={item} session={session} onDone={() => setMode('none')} />
      ) : null}
    </li>
  )
}

/** The observable facts, as recorded. Shown to the employee too (CRM10). */
function Facts({ detail }: { detail: Record<string, unknown> }) {
  const entries = Object.entries(detail ?? {})
  if (entries.length === 0) return null
  return (
    <dl className={ui.facts}>
      {entries.map(([key, value]) => (
        <div key={key} style={{ display: 'contents' }}>
          <dt>{humanise(key)}</dt>
          <dd>{formatFact(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function ExplainForm({
  item,
  session,
  onDone,
}: {
  item: WorkException
  session: SessionContext
  onDone: () => void
}) {
  const mutation = useExceptionResponse(session.workspace.id)
  const [explanation, setExplanation] = useState(item.employee_explanation)
  const [dispute, setDispute] = useState(item.state === 'disputed')

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      { exceptionId: item.id, mode: 'explain', explanation, dispute },
      { onSuccess: onDone },
    )
  }

  return (
    <form onSubmit={submit} className={ui.form} style={{ marginTop: 'var(--space-3)' }}>
      <label className={ui.field}>
        <span>What happened?</span>
        <TextArea
          limit={LIMITS.longText}
          rows={4}
          required
          value={explanation}
          onChange={setExplanation}
        />
      </label>
      <label className={ui.checkbox}>
        <input
          type="checkbox"
          checked={dispute}
          onChange={(event) => setDispute(event.target.checked)}
        />
        <span>I dispute these facts</span>
      </label>
      <ErrorNotice error={mutation.error} fallback="Your response could not be saved." />
      <div className={ui.actions}>
        <button type="submit" className={ui.primary} disabled={mutation.isPending}>
          {mutation.isPending ? 'Saving…' : dispute ? 'Send dispute' : 'Send explanation'}
        </button>
        <button type="button" className={ui.secondary} onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function ReviewForm({
  item,
  session,
  onDone,
}: {
  item: WorkException
  session: SessionContext
  onDone: () => void
}) {
  const mutation = useExceptionResponse(session.workspace.id)
  const [decision, setDecision] = useState('')
  const [dismiss, setDismiss] = useState(false)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        exceptionId: item.id,
        mode: 'review',
        decision,
        dismiss,
        expectedVersion: item.version,
      },
      { onSuccess: onDone },
    )
  }

  return (
    <form onSubmit={submit} className={ui.form} style={{ marginTop: 'var(--space-3)' }}>
      {!item.employee_explanation && item.subject ? (
        <p className={ui.warning}>
          The person concerned has not responded yet. Deciding now closes this
          without their account.
        </p>
      ) : null}
      <fieldset className={ui.fieldset}>
        <legend>Outcome</legend>
        <label className={ui.radio}>
          <input
            type="radio"
            name={`outcome-${item.id}`}
            checked={!dismiss}
            onChange={() => setDismiss(false)}
          />
          <span>Resolved — the issue was real and has been dealt with</span>
        </label>
        <label className={ui.radio}>
          <input
            type="radio"
            name={`outcome-${item.id}`}
            checked={dismiss}
            onChange={() => setDismiss(true)}
          />
          <span>Dismissed — on review, there was nothing to address</span>
        </label>
      </fieldset>
      <label className={ui.field}>
        <span>Your decision and the reason for it</span>
        <TextArea
          limit={LIMITS.longText}
          rows={3}
          required
          value={decision}
          onChange={setDecision}
        />
      </label>
      <ErrorNotice error={mutation.error} fallback="The decision could not be saved." />
      <div className={ui.actions}>
        <button type="submit" className={ui.primary} disabled={mutation.isPending}>
          {mutation.isPending ? 'Saving…' : 'Record decision'}
        </button>
        <button type="button" className={ui.secondary} onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function humanise(key: string): string {
  const text = key.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function formatFact(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value)) {
    return formatDateTime(value)
  }
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
