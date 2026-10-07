import { useEffect, useRef, useState } from 'react'

import styles from './CompleteTaskDialog.module.css'
import { TextArea, TextInput } from '@/components/fields'
import { LIMITS } from '@/lib/limits'
import { ApiError } from '@/lib/api'
import { useCompleteTask } from '@/lib/queries'
import type { Task } from '@/lib/types'

interface Props {
  task: Task
  workspaceId: string
  timeZone: string
  onClose: () => void
}

/**
 * Complete a follow-up and decide the next step in the same flow (CRM05).
 *
 * The server requires either a next action or an explicit stop reason, so the
 * form offers exactly that choice rather than letting a user complete work and
 * leave nothing scheduled — which is how deals go quiet.
 *
 * Three behaviours here come straight from section 6.2:
 * - The save is not optimistic. The row updates when the server confirms.
 * - A failed save keeps what the user typed, so nothing is retyped.
 * - The submit button is disabled while in flight, so a double click cannot
 *   produce a duplicate action.
 */
export function CompleteTaskDialog({ task, workspaceId, onClose }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const firstFieldRef = useRef<HTMLTextAreaElement>(null)

  const [outcome, setOutcome] = useState('')
  const [choice, setChoice] = useState<'next' | 'stop'>('next')
  const [nextTitle, setNextTitle] = useState('')
  const [nextDueAt, setNextDueAt] = useState(defaultNextDue())
  const [stopReason, setStopReason] = useState('')
  const [conflict, setConflict] = useState(false)

  const mutation = useCompleteTask(workspaceId)

  useEffect(() => {
    firstFieldRef.current?.focus()
  }, [])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const fieldErrors =
    mutation.error instanceof ApiError ? mutation.error.fieldErrors : {}

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setConflict(false)

    mutation.mutate(
      {
        taskId: task.id,
        outcome,
        expectedVersion: task.version,
        ...(choice === 'next'
          ? {
              nextActionTitle: nextTitle,
              nextActionDueAt: new Date(nextDueAt).toISOString(),
            }
          : { stopReason }),
      },
      {
        onSuccess: onClose,
        onError: (error) => {
          // Somebody else changed the task first. The user reloads and decides;
          // the client never retries silently over their change.
          if (error instanceof ApiError && error.isVersionConflict) {
            setConflict(true)
          }
        },
      },
    )
  }

  return (
    <div className={styles.backdrop} onMouseDown={onClose}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="complete-title"
        className={styles.dialog}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h2 id="complete-title">Record outcome</h2>
        <p className={styles.context}>
          {task.title}
          {task.contact_name ? ` · ${task.contact_name}` : ''}
        </p>

        {conflict ? (
          <p role="alert" className={styles.conflict}>
            This task changed while you were writing. Reload to see the current
            version, then reapply your update. Nothing you typed has been sent.
          </p>
        ) : null}

        {mutation.isError && !conflict ? (
          <p role="alert" className={styles.error}>
            {mutation.error instanceof ApiError
              ? mutation.error.message
              : 'The outcome could not be saved.'}
          </p>
        ) : null}

        <form onSubmit={handleSubmit} className={styles.form}>
          <label className={styles.field}>
            <span>What happened?</span>
            <TextArea
              limit={LIMITS.longText}
              ref={firstFieldRef}
              value={outcome}
              onChange={setOutcome}
              rows={3}
              aria-describedby={fieldErrors.outcome ? 'outcome-error' : undefined}
            />
            {fieldErrors.outcome ? (
              <span id="outcome-error" className={styles.fieldError}>
                {fieldErrors.outcome.join(' ')}
              </span>
            ) : null}
          </label>

          <fieldset className={styles.fieldset}>
            <legend>What happens next?</legend>
            <label className={styles.radio}>
              <input
                type="radio"
                name="next-step"
                checked={choice === 'next'}
                onChange={() => setChoice('next')}
              />
              <span>Schedule the next action</span>
            </label>
            <label className={styles.radio}>
              <input
                type="radio"
                name="next-step"
                checked={choice === 'stop'}
                onChange={() => setChoice('stop')}
              />
              <span>Stop here, and say why</span>
            </label>
          </fieldset>

          {choice === 'next' ? (
            <>
              <label className={styles.field}>
                <span>Next action</span>
                <TextInput
                  limit={LIMITS.title}
                  type="text"
                  value={nextTitle}
                  onChange={setNextTitle}
                  required
                />
              </label>
              <label className={styles.field}>
                <span>Due</span>
                <input
                  type="datetime-local"
                  value={nextDueAt}
                  onChange={(event) => setNextDueAt(event.target.value)}
                  required
                />
              </label>
            </>
          ) : (
            <label className={styles.field}>
              <span>Why is there no next action?</span>
              <TextArea
                limit={LIMITS.reason}
                value={stopReason}
                onChange={setStopReason}
                rows={2}
                required
              />
            </label>
          )}

          <div className={styles.actions}>
            <button type="button" onClick={onClose} className={styles.secondary}>
              Cancel
            </button>
            <button
              type="submit"
              className={styles.primary}
              disabled={mutation.isPending}
            >
              {mutation.isPending ? 'Saving…' : 'Save outcome'}
            </button>
          </div>
          {/* Announced to assistive technology without stealing focus. */}
          <p aria-live="polite" className="visually-hidden">
            {mutation.isPending ? 'Saving your outcome.' : ''}
          </p>
        </form>
      </div>
    </div>
  )
}

/** Default the next action to tomorrow morning, in the browser's local zone. */
function defaultNextDue(): string {
  const tomorrow = new Date()
  tomorrow.setDate(tomorrow.getDate() + 1)
  tomorrow.setHours(10, 0, 0, 0)
  const offset = tomorrow.getTimezoneOffset() * 60_000
  return new Date(tomorrow.getTime() - offset).toISOString().slice(0, 16)
}
