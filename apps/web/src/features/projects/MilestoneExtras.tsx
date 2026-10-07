import { useState } from 'react'

import { TextInput } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { MemberSelect } from '@/components/MemberSelect'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { formatDateTime, localDateTimeFromNow, localToIso } from '@/lib/dates'
import { useAddDependency, useCreateTask, useMilestoneTasks } from '@/lib/queries'
import type { Milestone, SessionContext } from '@/lib/types'

/**
 * The tasks inside one milestone (CRM08: projects contain milestones *and*
 * tasks). Completing them happens on Today, like any other task.
 */
export function MilestoneTasks({
  milestone,
  session,
}: {
  milestone: Milestone
  session: SessionContext
}) {
  const { data } = useMilestoneTasks(session.workspace.id, milestone.id)
  const create = useCreateTask(session.workspace.id)
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('')
  const [dueAt, setDueAt] = useState(localDateTimeFromNow(2))
  const [owner, setOwner] = useState(milestone.owner ?? session.user_id)
  const canAssign = session.permissions.includes('task.assign_others')
  const tasks = data?.results ?? []
  const closed = milestone.status === 'accepted' || milestone.status === 'cancelled'

  return (
    <div>
      {tasks.length > 0 ? (
        <ul className={ui.list} style={{ gap: 'var(--space-1)', marginBottom: 'var(--space-2)', fontSize: 'var(--text-sm)' }}>
          {tasks.map((task) => (
            <li key={task.id}>
              {task.title}{' '}
              <span className={ui.muted}>
                · {task.status === 'completed' ? 'done' : `due ${formatDateTime(task.due_at)}`}
                {task.owner_email ? ` · ${task.owner_email}` : ''}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {!closed && session.permissions.includes('task.manage') ? (
        adding ? (
          <form
            className={ui.form}
            style={{ marginTop: 'var(--space-2)' }}
            onSubmit={(event) => {
              event.preventDefault()
              create.mutate(
                {
                  title,
                  dueAt: localToIso(dueAt),
                  kind: 'delivery',
                  description: '',
                  owner: canAssign ? owner : session.user_id,
                  milestone: milestone.id,
                },
                {
                  onSuccess: () => {
                    setTitle('')
                    setAdding(false)
                  },
                },
              )
            }}
          >
            <div className={ui.formGrid}>
              <label className={ui.field}>
                <span>Task</span>
                <TextInput limit={LIMITS.title} type="text" required value={title} onChange={setTitle} />
              </label>
              <label className={ui.field}>
                <span>Due</span>
                <input type="datetime-local" required value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
              </label>
              {canAssign ? (
                <label className={ui.field}>
                  <span>Owner</span>
                  <MemberSelect workspaceId={session.workspace.id} value={owner} onChange={setOwner} required />
                </label>
              ) : null}
            </div>
            <ErrorNotice error={create.error} fallback="The task could not be added." />
            <div className={ui.actions}>
              <button type="submit" className={ui.primary} disabled={create.isPending}>
                {create.isPending ? 'Adding…' : 'Add task'}
              </button>
              <button type="button" className={ui.secondary} onClick={() => setAdding(false)}>
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <button
            type="button"
            className={`${ui.secondary} ${ui.small}`}
            aria-label={`Add a task to ${milestone.name}`}
            onClick={() => setAdding(true)}
          >
            Add task
          </button>
        )
      ) : null}
    </div>
  )
}

/**
 * Make one milestone wait on another. The server rejects a cycle, and the
 * error says so.
 */
export function AddDependency({
  milestone,
  siblings,
  session,
}: {
  milestone: Milestone
  siblings: Milestone[]
  session: SessionContext
}) {
  const mutation = useAddDependency(session.workspace.id)
  const [target, setTarget] = useState('')
  const candidates = siblings.filter((m) => m.id !== milestone.id && m.status !== 'cancelled')

  if (!session.permissions.includes('project.manage') || candidates.length === 0) return null
  if (milestone.status === 'accepted' || milestone.status === 'cancelled') return null

  return (
    <form
      className={ui.form}
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate(
          { milestoneId: milestone.id, dependsOn: target },
          { onSuccess: () => setTarget('') },
        )
      }}
    >
      <div className={ui.actions} style={{ alignItems: 'end' }}>
        <label className={ui.field}>
          <span>Waits on</span>
          <select required value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Choose a milestone</option>
            {candidates.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className={ui.secondary} disabled={mutation.isPending || !target}>
          Add dependency
        </button>
      </div>
      <ErrorNotice error={mutation.error} fallback="The dependency could not be added." />
    </form>
  )
}
