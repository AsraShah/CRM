import { useState } from 'react'
import { Link } from 'react-router'

import styles from './TodayPage.module.css'
import { TextInput } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { useAlertAction } from '@/lib/queries'
import type { Notification } from '@/lib/types'

/**
 * Where the record behind an alert lives. Leads and tickets open the record
 * itself; the others open the screen that holds it.
 */
export function recordLink(entityType: string, entityId: string | null): string | null {
  if (!entityId) return null
  switch (entityType) {
    case 'lead':
      return `/leads?lead=${entityId}`
    case 'ticket':
      return `/tickets?ticket=${entityId}`
    case 'opportunity':
      return '/pipeline'
    case 'client':
      return `/clients/${entityId}`
    case 'milestone':
    case 'project':
      return '/projects'
    case 'task':
      return '/today'
    default:
      return null
  }
}

/**
 * One alert (CRM06). It says why it appeared, opens the record that caused it,
 * and can be acknowledged ("I have seen this") or resolved with a note saying
 * what was done. The two are kept separate: seeing is not dealing with.
 */
export function AlertItem({ alert, workspaceId }: { alert: Notification; workspaceId: string }) {
  const mutation = useAlertAction(workspaceId)
  const [resolving, setResolving] = useState(false)
  const [note, setNote] = useState('')
  const link = recordLink(alert.entity_type, alert.entity_id)

  return (
    <li className={styles.alert}>
      <p className={styles.alertTitle}>{alert.title}</p>
      <p className={styles.alertCause}>{alert.cause}</p>
      {alert.acknowledged_at ? <span className={ui.pill}>Acknowledged</span> : null}
      <ErrorNotice error={mutation.error} fallback="The alert could not be updated." />
      {resolving ? (
        <form
          className={ui.form}
          onSubmit={(event) => {
            event.preventDefault()
            mutation.mutate({ alertId: alert.id, action: 'resolve', note })
          }}
        >
          <label className={ui.field}>
            <span>What was done?</span>
            <TextInput limit={LIMITS.reason} type="text" required value={note} onChange={setNote} />
          </label>
          <div className={ui.actions}>
            <button type="submit" className={ui.primary} disabled={mutation.isPending}>
              {mutation.isPending ? 'Saving…' : 'Resolve'}
            </button>
            <button type="button" className={ui.secondary} onClick={() => setResolving(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className={ui.actions}>
          {link ? (
            <Link className={`${ui.secondary} ${ui.small}`} to={link}>
              Open the record
            </Link>
          ) : null}
          {!alert.acknowledged_at ? (
            <button
              type="button"
              className={`${ui.ghost} ${ui.small}`}
              disabled={mutation.isPending}
              onClick={() => mutation.mutate({ alertId: alert.id, action: 'acknowledge' })}
            >
              Acknowledge
            </button>
          ) : null}
          <button type="button" className={`${ui.ghost} ${ui.small}`} onClick={() => setResolving(true)}>
            Resolve
          </button>
        </div>
      )}
    </li>
  )
}
