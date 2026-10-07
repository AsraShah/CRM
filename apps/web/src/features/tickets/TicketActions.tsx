import { useState } from 'react'

import { TextArea, TextInput } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import { MemberSelect } from '@/components/MemberSelect'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { localDateTimeFromNow, localToIso } from '@/lib/dates'
import { useTicketNextAction, useTicketTransition } from '@/lib/queries'
import type { SessionContext, Ticket, TicketState } from '@/lib/types'

/** Mirrors support.services.ALLOWED_TICKET_TRANSITIONS. */
const ALLOWED: Record<TicketState, TicketState[]> = {
  new: ['triaged', 'closed'],
  triaged: ['in_progress', 'waiting_customer', 'waiting_internal', 'closed'],
  in_progress: ['waiting_customer', 'waiting_internal', 'resolved'],
  waiting_customer: ['in_progress', 'resolved', 'closed'],
  waiting_internal: ['in_progress', 'resolved'],
  // Reopening returns to triage, so somebody looks at why it came back.
  resolved: ['closed', 'triaged'],
  closed: ['triaged'],
}

function actionLabel(from: TicketState, to: TicketState): string {
  if (to === 'triaged') return from === 'new' ? 'Triage' : 'Reopen'
  if (to === 'in_progress') return from === 'triaged' ? 'Start work' : 'Resume work'
  if (to === 'waiting_customer') return 'Wait on the customer'
  if (to === 'waiting_internal') return 'Wait on us'
  if (to === 'resolved') return 'Resolve'
  return 'Close'
}

/**
 * Ticket state changes (CRM09).
 *
 * Each target collects what the server demands before the request goes:
 * waiting needs a reason, a next owner and a review time, so a waiting ticket
 * cannot wait forever unnoticed; resolving needs a note *and* the result of a
 * closure test, because "fixed" without a check is a claim.
 */
export function TicketActions({
  ticket,
  session,
}: {
  ticket: Ticket
  session: SessionContext
}) {
  const [target, setTarget] = useState<TicketState | null>(null)
  if (!session.permissions.includes('ticket.manage')) return null

  const targets = ALLOWED[ticket.state] ?? []
  if (targets.length === 0) return null

  return (
    <>
      <div className={ui.actions}>
        {targets.map((to, index) => (
          <button
            key={to}
            type="button"
            className={index === 0 ? ui.primary : ui.secondary}
            onClick={() => setTarget(to)}
          >
            {actionLabel(ticket.state, to)}
          </button>
        ))}
      </div>
      {target ? (
        <TransitionDialog
          ticket={ticket}
          target={target}
          session={session}
          onClose={() => setTarget(null)}
        />
      ) : null}
    </>
  )
}

function TransitionDialog({
  ticket,
  target,
  session,
  onClose,
}: {
  ticket: Ticket
  target: TicketState
  session: SessionContext
  onClose: () => void
}) {
  const mutation = useTicketTransition(session.workspace.id)
  const [reason, setReason] = useState('')
  const [waitingReason, setWaitingReason] = useState('')
  const [nextOwner, setNextOwner] = useState(ticket.owner ?? '')
  const [reviewAt, setReviewAt] = useState(localDateTimeFromNow(2))
  const [resolutionNote, setResolutionNote] = useState('')
  const [closureTest, setClosureTest] = useState('')

  const waiting = target === 'waiting_customer' || target === 'waiting_internal'
  const resolving = target === 'resolved'
  const reopening = target === 'triaged' && ticket.state !== 'new'

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        ticketId: ticket.id,
        targetState: target,
        expectedVersion: ticket.version,
        reason,
        ...(waiting
          ? {
              waitingReason,
              waitingNextOwnerId: nextOwner,
              waitingReviewAt: localToIso(reviewAt),
            }
          : {}),
        ...(resolving
          ? { resolutionNote, closureTestResult: closureTest }
          : {}),
      },
      { onSuccess: onClose },
    )
  }

  return (
    <Dialog title={actionLabel(ticket.state, target)} context={ticket.title} onClose={onClose}>
      <form onSubmit={submit} className={ui.form}>
        {waiting ? (
          <>
            <label className={ui.field}>
              <span>
                {target === 'waiting_customer'
                  ? 'What do we need from the customer?'
                  : 'What are we waiting for internally?'}
              </span>
              <TextArea
                limit={LIMITS.reason}
                rows={2}
                required
                value={waitingReason}
                onChange={setWaitingReason}
              />
            </label>
            <div className={ui.formGrid}>
              <label className={ui.field}>
                <span>Who follows it up?</span>
                <MemberSelect
                  workspaceId={session.workspace.id}
                  value={nextOwner}
                  onChange={setNextOwner}
                  required
                />
              </label>
              <label className={ui.field}>
                <span>Review by</span>
                <input
                  type="datetime-local"
                  required
                  value={reviewAt}
                  onChange={(event) => setReviewAt(event.target.value)}
                />
              </label>
            </div>
          </>
        ) : null}

        {resolving ? (
          <>
            <label className={ui.field}>
              <span>Resolution</span>
              <span className={ui.hint}>What was wrong and what was done.</span>
              <TextArea
                limit={LIMITS.longText}
                rows={3}
                required
                value={resolutionNote}
                onChange={setResolutionNote}
              />
            </label>
            <label className={ui.field}>
              <span>Closure test</span>
              <span className={ui.hint}>
                How the fix was checked, and the result.
              </span>
              <TextArea
                limit={LIMITS.reason}
                rows={2}
                required
                value={closureTest}
                onChange={setClosureTest}
              />
            </label>
          </>
        ) : null}

        {reopening ? (
          <p className={ui.muted} style={{ margin: 0 }}>
            The earlier resolution is kept, and the ticket goes back to triage.
          </p>
        ) : null}

        {!waiting && !resolving ? (
          <label className={ui.field}>
            <span>{reopening ? 'Why is it being reopened?' : 'Note (optional)'}</span>
            <TextArea
              limit={LIMITS.reason}
              rows={2}
              required={reopening}
              value={reason}
              onChange={setReason}
            />
          </label>
        ) : null}

        <ErrorNotice error={mutation.error} fallback="The ticket could not be updated." />
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : actionLabel(ticket.state, target)}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

/**
 * What happens next on this ticket (CRM09). Changes far more often than the
 * state, so it is edited in place rather than through a transition.
 */
export function NextActionField({ ticket, session }: { ticket: Ticket; session: SessionContext }) {
  const mutation = useTicketNextAction(session.workspace.id)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(ticket.next_action)
  const canEdit = session.permissions.includes('ticket.manage') && ticket.is_open

  if (!editing) {
    return (
      <p
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 'var(--space-2)',
          margin: 0,
          padding: 'var(--space-3) var(--space-4)',
          fontSize: 'var(--text-sm)',
          background: 'var(--colour-surface-sunken)',
          border: '1px solid var(--colour-border)',
          borderRadius: 'var(--radius-md)',
        }}
      >
        <strong>Next action:</strong>{' '}
        {ticket.next_action || <span className={ui.muted}>None recorded</span>}
        {canEdit ? (
          <>
            {' '}
            <button
              type="button"
              className={`${ui.secondary} ${ui.small}`}
              style={{ marginLeft: 'auto' }}
              onClick={() => {
                setValue(ticket.next_action)
                setEditing(true)
              }}
            >
              {ticket.next_action ? 'Change' : 'Set'}
            </button>
          </>
        ) : null}
      </p>
    )
  }

  return (
    <form
      className={ui.form}
      style={{ marginBottom: 'var(--space-3)' }}
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate(
          { ticketId: ticket.id, expectedVersion: ticket.version, nextAction: value },
          { onSuccess: () => setEditing(false) },
        )
      }}
    >
      <label className={ui.field}>
        <span>Next action</span>
        <TextInput limit={LIMITS.title} type="text" value={value} onChange={setValue} />
      </label>
      <ErrorNotice error={mutation.error} fallback="The next action could not be saved." />
      <div className={ui.actions}>
        <button type="submit" className={ui.primary} disabled={mutation.isPending}>
          {mutation.isPending ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className={ui.secondary} onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
    </form>
  )
}
