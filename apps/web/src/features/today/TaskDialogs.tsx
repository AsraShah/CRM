import { useState } from 'react'

import { TextArea, TextInput } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import { MemberSelect } from '@/components/MemberSelect'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { formatDateTime, localDateTimeFromNow, localToIso } from '@/lib/dates'
import { useCreateTask, useRescheduleTask } from '@/lib/queries'
import type { SessionContext, Task } from '@/lib/types'

const TASK_KINDS: Record<string, string> = {
  follow_up: 'Follow-up',
  call: 'Call',
  email: 'Email',
  meeting: 'Meeting',
  admin: 'Administrative',
  delivery: 'Delivery work',
}

/**
 * Move a deadline (CRM05).
 *
 * The original due time is shown and kept: a rescheduled task carries both
 * dates, so moving a commitment never quietly erases it. A reason is required,
 * and repeated rescheduling is something rule A08 notices.
 */
export function RescheduleDialog({
  task,
  workspaceId,
  onClose,
}: {
  task: Task
  workspaceId: string
  onClose: () => void
}) {
  const mutation = useRescheduleTask(workspaceId)
  const [dueAt, setDueAt] = useState(localDateTimeFromNow(1))
  const [reason, setReason] = useState('')

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        taskId: task.id,
        newDueAt: localToIso(dueAt),
        reason,
        expectedVersion: task.version,
      },
      { onSuccess: onClose },
    )
  }

  return (
    <Dialog title="Reschedule" context={task.title} onClose={onClose}>
      <form onSubmit={submit} className={ui.form}>
        <dl className={ui.facts} style={{ margin: 0 }}>
          <dt>Due now</dt>
          <dd>{formatDateTime(task.due_at)}</dd>
          {task.reschedule_count > 0 ? (
            <>
              <dt>Originally due</dt>
              <dd>
                {formatDateTime(task.original_due_at)} · moved {task.reschedule_count}{' '}
                {task.reschedule_count === 1 ? 'time' : 'times'} already
              </dd>
            </>
          ) : null}
        </dl>
        <label className={ui.field}>
          <span>New due time</span>
          <input
            type="datetime-local"
            required
            value={dueAt}
            onChange={(event) => setDueAt(event.target.value)}
          />
        </label>
        <label className={ui.field}>
          <span>Why is it moving?</span>
          <TextArea
            limit={LIMITS.shortReason}
            rows={2}
            required
            value={reason}
            onChange={setReason}
          />
        </label>
        <p className={ui.hint} style={{ margin: 0 }}>
          The original deadline stays on record alongside the new one.
        </p>
        <ErrorNotice error={mutation.error} fallback="The task could not be rescheduled." />
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Reschedule'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

export function NewTaskDialog({
  session,
  onClose,
}: {
  session: SessionContext
  onClose: () => void
}) {
  const mutation = useCreateTask(session.workspace.id)
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState('follow_up')
  const [dueAt, setDueAt] = useState(localDateTimeFromNow(1))
  const [description, setDescription] = useState('')
  const [owner, setOwner] = useState(session.user_id)
  const canAssignOthers = session.permissions.includes('task.assign_others')

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      { title, kind, dueAt: localToIso(dueAt), description, owner },
      { onSuccess: onClose },
    )
  }

  return (
    <Dialog title="New task" onClose={onClose}>
      <form onSubmit={submit} className={ui.form}>
        <label className={ui.field}>
          <span>What needs doing?</span>
          <TextInput
            limit={LIMITS.title}
            type="text"
            required
            value={title}
            onChange={setTitle}
          />
        </label>
        <div className={ui.formGrid}>
          <label className={ui.field}>
            <span>Kind</span>
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              {Object.entries(TASK_KINDS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className={ui.field}>
            <span>Due</span>
            <input
              type="datetime-local"
              required
              value={dueAt}
              onChange={(event) => setDueAt(event.target.value)}
            />
          </label>
          {canAssignOthers ? (
            <label className={ui.field}>
              <span>Owner</span>
              <MemberSelect
                workspaceId={session.workspace.id}
                value={owner}
                onChange={setOwner}
                required
              />
            </label>
          ) : null}
        </div>
        <label className={ui.field}>
          <span>Details (optional)</span>
          <TextArea
            limit={LIMITS.longText}
            rows={3}
            value={description}
            onChange={setDescription}
          />
        </label>
        <ErrorNotice error={mutation.error} fallback="The task could not be created." />
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Create task'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
