import { AlertTriangle, Bot, Briefcase, Clock, RotateCcw, User } from 'lucide-react'

import styles from './TodayPage.module.css'
import ui from '@/components/ui.module.css'
import type { Task } from '@/lib/types'

interface TaskRowProps {
  task: Task
  onComplete: (task: Task) => void
  /** Omitted where moving the deadline is not offered. */
  onReschedule?: (task: Task) => void
}

/**
 * One item of work.
 *
 * Shows the customer, the purpose, the due time and the relevant context, with
 * one primary action (SVX-PRD-001 section 7.2). A rescheduled task also shows
 * that its deadline moved — the original commitment stays visible rather than
 * being replaced by the latest date (CRM05).
 */
export function TaskRow({ task, onComplete, onReschedule }: TaskRowProps) {
  const due = new Date(task.due_at)
  const wasRescheduled = task.reschedule_count > 0

  return (
    <li className={task.is_overdue ? `${styles.row} ${styles.rowOverdue}` : styles.row}>
      <div className={styles.rowMain}>
        <p className={styles.rowTitle}>{task.title}</p>
        <p className={styles.rowContext}>
          {task.contact_name ? (
            <span className={styles.chip}>
              <User size={12} aria-hidden="true" />
              <span>{task.contact_name}</span>
            </span>
          ) : null}
          {task.opportunity_service ? (
            <span className={styles.chip}>
              <Briefcase size={12} aria-hidden="true" />
              <span>{task.opportunity_service}</span>
            </span>
          ) : null}
          {task.origin === 'rule' ? (
            // Distinguishes a commitment the person made from one a rule
            // created for them (CRM10).
            <span className={styles.chip}>
              <Bot size={12} aria-hidden="true" />
              <span>Created automatically</span>
            </span>
          ) : null}
        </p>
      </div>

      <div className={styles.rowMeta}>
        <p className={task.is_overdue ? styles.dueOverdue : styles.due}>
          {task.is_overdue ? (
            <AlertTriangle size={14} aria-hidden="true" />
          ) : (
            <Clock size={14} aria-hidden="true" />
          )}
          {/* The icon repeats the word; it is never the only signal. */}
          <span>
            {task.is_overdue ? 'Overdue — due ' : 'Due '}
            <time dateTime={task.due_at}>{formatDateTime(due)}</time>
          </span>
        </p>
        {wasRescheduled ? (
          <p className={styles.rescheduled}>
            <RotateCcw size={12} aria-hidden="true" />
            <span>
              Moved {task.reschedule_count}
              {task.reschedule_count === 1 ? ' time' : ' times'}; originally due{' '}
              <time dateTime={task.original_due_at}>
                {formatDateTime(new Date(task.original_due_at))}
              </time>
            </span>
          </p>
        ) : null}
      </div>

      <div className={styles.rowActions}>
        {/* The accessible name starts with the visible text (WCAG 2.5.3) and
            names the task, so a list of identical buttons is unambiguous. */}
        <button
          type="button"
          className={`${ui.primary} ${ui.small}`}
          aria-label={`Record outcome for ${task.title}`}
          onClick={() => onComplete(task)}
        >
          Record outcome
        </button>
        {onReschedule ? (
          <button
            type="button"
            className={`${ui.ghost} ${ui.small}`}
            aria-label={`Reschedule ${task.title}`}
            onClick={() => onReschedule(task)}
          >
            Reschedule
          </button>
        ) : null}
      </div>
    </li>
  )
}

/** Render a UTC instant in the viewer's locale. The stored value is unchanged. */
function formatDateTime(value: Date): string {
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: 'numeric',
    minute: '2-digit',
  }).format(value)
}
