import { useState } from 'react'

import { TextArea } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import { MemberSelect } from '@/components/MemberSelect'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { useMilestoneAction } from '@/lib/queries'
import type { Milestone, SessionContext } from '@/lib/types'

type Action = 'start' | 'submit' | 'accept' | 'reopen' | 'block' | 'cancel' | 'send-back'

/**
 * The actions a milestone offers, given its status and the viewer's role.
 *
 * Only what the server would accept is shown (delivery.ALLOWED_MILESTONE_
 * TRANSITIONS). Accepting is offered only to a reviewer, and only on submitted
 * work — submission and acceptance stay separate acts (CRM08).
 */
export function MilestoneActions({
  milestone,
  session,
}: {
  milestone: Milestone
  session: SessionContext
}) {
  const [open, setOpen] = useState<Action | null>(null)
  const canAccept = session.permissions.includes('milestone.accept')
  const canSubmit = session.permissions.includes('milestone.submit')
  const canManage = session.permissions.includes('project.manage')

  const offered: { action: Action; label: string; primary?: boolean }[] = []
  const { status } = milestone
  if ((status === 'planned' || status === 'ready') && canSubmit) {
    offered.push({ action: 'start', label: 'Start work', primary: true })
  }
  if (status === 'blocked' && canSubmit) {
    offered.push({ action: 'start', label: 'Resume work', primary: true })
  }
  if (status === 'in_progress' && canSubmit) {
    offered.push({ action: 'submit', label: 'Submit for review', primary: true })
  }
  if (status === 'in_review' && canAccept) {
    offered.push({ action: 'accept', label: 'Review and accept', primary: true })
  }
  if (status === 'in_review' && canAccept) {
    offered.push({ action: 'send-back', label: 'Send back' })
  }
  // Blocking names a next owner, so it is a manager's call (project.manage).
  if ((status === 'ready' || status === 'in_progress') && canManage) {
    offered.push({ action: 'block', label: 'Mark blocked' })
  }
  if (status === 'accepted' && canAccept) {
    offered.push({ action: 'reopen', label: 'Reopen' })
  }
  // Cancellation needs a reason and its impact, and is a manager's decision.
  if (
    (status === 'planned' || status === 'ready' || status === 'in_progress' || status === 'blocked') &&
    canManage
  ) {
    offered.push({ action: 'cancel', label: 'Cancel milestone' })
  }

  if (offered.length === 0) return null

  return (
    <>
      <div className={ui.actions}>
        {offered.map(({ action, label, primary }) => (
          <button
            key={action}
            type="button"
            className={`${primary ? ui.primary : action === 'cancel' ? ui.ghost : ui.secondary} ${ui.small}`}
            aria-label={`${label}: ${milestone.name}`}
            onClick={() => setOpen(action)}
          >
            {label}
          </button>
        ))}
      </div>
      {open ? (
        <MilestoneDialog
          action={open}
          milestone={milestone}
          session={session}
          onClose={() => setOpen(null)}
        />
      ) : null}
    </>
  )
}

const TITLES: Record<Action, string> = {
  start: 'Start work',
  submit: 'Submit for review',
  accept: 'Review and accept',
  reopen: 'Reopen accepted work',
  block: 'Mark blocked',
  cancel: 'Cancel milestone',
  'send-back': 'Send back',
}

function MilestoneDialog({
  action,
  milestone,
  session,
  onClose,
}: {
  action: Action
  milestone: Milestone
  session: SessionContext
  onClose: () => void
}) {
  const mutation = useMilestoneAction(session.workspace.id)
  const [evidence, setEvidence] = useState('')
  const [note, setNote] = useState('')
  const [reason, setReason] = useState('')
  const [blockedReason, setBlockedReason] = useState('')
  const [nextOwner, setNextOwner] = useState('')
  const [override, setOverride] = useState(false)
  const [overrideReason, setOverrideReason] = useState('')
  const [impact, setImpact] = useState('')

  const title =
    action === 'start' && milestone.status === 'blocked' ? 'Resume work' : TITLES[action]
  const hasUnmet = milestone.unmet_dependencies.length > 0
  // Self-acceptance is not blocked by the server for a manager who also did the
  // work, but it should never happen by accident.
  const acceptingOwnWork =
    action === 'accept' && milestone.submitted_by === session.user_id

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        milestoneId: milestone.id,
        action,
        expectedVersion: milestone.version,
        evidence,
        note,
        reason,
        blockedReason,
        nextOwnerId: nextOwner,
        overrideDependencies: override,
        overrideReason,
        impact,
      },
      { onSuccess: onClose },
    )
  }

  return (
    <Dialog title={title} context={milestone.name} onClose={onClose}>
      <form onSubmit={submit} className={ui.form}>
        {action === 'start' ? (
          <p className={ui.muted}>
            {milestone.status === 'blocked'
              ? 'This records that the blocker is cleared and work has resumed.'
              : 'This records that work has begun. It does not change who owns it.'}
          </p>
        ) : null}

        {action === 'submit' ? (
          <label className={ui.field}>
            <span>Evidence the work is done</span>
            <span className={ui.hint}>
              Links, file names or a description a reviewer can check. A
              reviewer accepts it separately.
            </span>
            <TextArea
              limit={LIMITS.longText}
              rows={4}
              required
              value={evidence}
              onChange={setEvidence}
            />
          </label>
        ) : null}

        {action === 'accept' ? (
          <>
            <div className={ui.notice}>
              <strong>Submitted evidence:</strong>
              <p style={{ margin: 'var(--space-1) 0 0', whiteSpace: 'pre-wrap' }}>
                {milestone.evidence || 'None recorded.'}
              </p>
            </div>
            {acceptingOwnWork ? (
              <p className={ui.warning}>
                You submitted this work yourself. Accepting it means nobody else
                has reviewed it.
              </p>
            ) : null}
            <label className={ui.field}>
              <span>Acceptance note (optional)</span>
              <TextArea limit={LIMITS.reason} rows={2} value={note} onChange={setNote} />
            </label>
            {hasUnmet ? (
              <fieldset className={ui.fieldset}>
                <legend>Unmet dependencies</legend>
                <p className={ui.warning}>
                  Still waiting on{' '}
                  {milestone.unmet_dependencies.map((d) => d.name).join(', ')}.
                </p>
                <label className={ui.checkbox}>
                  <input
                    type="checkbox"
                    checked={override}
                    onChange={(event) => setOverride(event.target.checked)}
                  />
                  <span>Accept anyway, as a recorded manager override</span>
                </label>
                {override ? (
                  <label className={ui.field}>
                    <span>Why is overriding the dependency acceptable?</span>
                    <TextArea
                      limit={LIMITS.reason}
                      rows={2}
                      required
                      value={overrideReason}
                      onChange={setOverrideReason}
                    />
                  </label>
                ) : null}
              </fieldset>
            ) : null}
          </>
        ) : null}

        {action === 'reopen' ? (
          <label className={ui.field}>
            <span>Why is it being reopened?</span>
            <span className={ui.hint}>
              The original acceptance and its evidence are kept.
            </span>
            <TextArea
              limit={LIMITS.reason}
              rows={3}
              required
              value={reason}
              onChange={setReason}
            />
          </label>
        ) : null}

        {action === 'send-back' ? (
          <>
            <div className={ui.notice}>
              <strong>Submitted evidence:</strong>
              <p style={{ margin: 'var(--space-1) 0 0', whiteSpace: 'pre-wrap' }}>
                {milestone.evidence || 'None recorded.'}
              </p>
            </div>
            <label className={ui.field}>
              <span>What must change before it can be accepted?</span>
              <span className={ui.hint}>
                The evidence is kept, with this note on top, for the next review.
              </span>
              <TextArea
                limit={LIMITS.reason}
                rows={3}
                required
                value={reason}
                onChange={setReason}
              />
            </label>
          </>
        ) : null}

        {action === 'cancel' ? (
          <>
            <label className={ui.field}>
              <span>Why is it cancelled?</span>
              <TextArea limit={LIMITS.reason} rows={2} required value={reason} onChange={setReason} />
            </label>
            <label className={ui.field}>
              <span>What does this change for the client or the plan?</span>
              <TextArea limit={LIMITS.reason} rows={2} required value={impact} onChange={setImpact} />
            </label>
            <p className={ui.warning} style={{ margin: 0 }}>
              A cancelled milestone cannot be restarted. The record stays, with
              this reason and impact.
            </p>
          </>
        ) : null}

        {action === 'block' ? (
          <>
            <label className={ui.field}>
              <span>What is blocking it?</span>
              <TextArea
                limit={LIMITS.reason}
                rows={3}
                required
                value={blockedReason}
                onChange={setBlockedReason}
              />
            </label>
            <label className={ui.field}>
              <span>Who acts next to unblock it?</span>
              <MemberSelect
                workspaceId={session.workspace.id}
                value={nextOwner}
                onChange={setNextOwner}
                required
              />
            </label>
          </>
        ) : null}

        <ErrorNotice error={mutation.error} fallback="The milestone could not be updated." />

        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className={ui.primary}
            disabled={mutation.isPending || (action === 'accept' && hasUnmet && !override)}
          >
            {mutation.isPending ? 'Saving…' : title}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
