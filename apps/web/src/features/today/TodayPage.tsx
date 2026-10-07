import { Bell, CalendarCheck, Plus } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext } from 'react-router'

import { AlertItem } from './AlertItem'
import { CompleteTaskDialog } from './CompleteTaskDialog'
import { NewTaskDialog, RescheduleDialog } from './TaskDialogs'
import styles from './TodayPage.module.css'
import { TaskRow } from './TaskRow'
import { EmptyState, PageHeader, Stat } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { useToday } from '@/lib/queries'
import type { SessionContext, Task } from '@/lib/types'

/**
 * The Today screen (CRM05, SVX-PRD-001 section 7.1).
 *
 * "An employee should open Today and understand what to do without consulting a
 * manager." Each item shows the customer, the purpose, the due time, the
 * relevant context and one primary action, and completing it records the
 * outcome and the next step without leaving the page.
 *
 * Overdue is a separate section from due-today deliberately. Merging them would
 * hide how far behind somebody is, which is the one thing this screen exists to
 * surface.
 */
export function TodayPage() {
  const session = useOutletContext<SessionContext>()
  const { data, isLoading, isError, refetch } = useToday(session.workspace.id)
  const [completing, setCompleting] = useState<Task | null>(null)
  const [rescheduling, setRescheduling] = useState<Task | null>(null)
  const [creating, setCreating] = useState(false)

  if (isLoading) {
    return (
      <p role="status" aria-live="polite" className={ui.muted}>
        Loading your work…
      </p>
    )
  }

  if (isError || !data) {
    return (
      <div role="alert" className={ui.card}>
        <h1 style={{ marginTop: 0 }}>Today</h1>
        <p>Your work could not be loaded.</p>
        <button type="button" className={ui.secondary} onClick={() => void refetch()}>
          Try again
        </button>
      </div>
    )
  }

  const firstName = (session.full_name || '').split(' ')[0]
  const today = new Intl.DateTimeFormat(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    timeZone: data.workspace_time_zone,
  }).format(new Date(data.server_time))
  const totalOpen =
    data.overdue.length + data.due_now.length + data.blocked.length + data.upcoming.length

  const sections: { id: string; title: string; tone: Tone; tasks: Task[] }[] = [
    { id: 'overdue', title: 'Overdue', tone: 'danger', tasks: data.overdue },
    { id: 'due-now', title: 'Due today', tone: 'warning', tasks: data.due_now },
    { id: 'blocked', title: 'Blocked', tone: 'info', tasks: data.blocked },
    { id: 'upcoming', title: 'Upcoming', tone: 'neutral', tasks: data.upcoming },
  ]

  return (
    <div className={ui.page}>
      <PageHeader
        eyebrow={today}
        title="Today"
        subtitle={
          firstName
            ? `Good to see you, ${firstName}. Times are shown in ${data.workspace_time_zone}.`
            : `Times are shown in ${data.workspace_time_zone}.`
        }
        actions={
          session.permissions.includes('task.manage') ? (
            <button type="button" className={ui.primary} onClick={() => setCreating(true)}>
              <Plus size={16} aria-hidden="true" />
              New task
            </button>
          ) : null
        }
      />

      <div className={ui.stats}>
        <Stat feature label="Overdue" value={data.overdue.length} meta="Past their due time" />
        <Stat label="Due today" value={data.due_now.length} meta="Before the day ends" />
        <Stat label="Blocked" value={data.blocked.length} meta="Waiting on something" />
        <Stat label="Upcoming" value={data.upcoming.length} meta="Scheduled later" />
      </div>

      <div className={styles.layout}>
        <div className={styles.tasks}>
          {totalOpen === 0 ? (
            <div className={ui.card}>
              <EmptyState icon={<CalendarCheck size={22} aria-hidden="true" />} title="You're all clear">
                Nothing is due and nothing is scheduled. New follow-ups appear here
                as soon as they are created.
              </EmptyState>
            </div>
          ) : (
            sections
              // Empty sections collapse; overdue always shows, because "nothing
              // overdue" is itself the most useful line on the page.
              .filter((s) => s.tasks.length > 0 || s.id === 'overdue')
              .map((s) => (
                <TaskSection
                  key={s.id}
                  {...s}
                  onComplete={setCompleting}
                  onReschedule={setRescheduling}
                />
              ))
          )}
        </div>

        <aside aria-labelledby="alerts-heading" className={styles.alertsColumn}>
          <div className={`${ui.card} ${ui.cardFlush}`}>
            <div className={styles.panelHeader}>
              <h2 id="alerts-heading">
                <Bell size={16} aria-hidden="true" /> Alerts
                <span className={styles.count}> ({data.alerts.length})</span>
              </h2>
            </div>
            {data.alerts.length === 0 ? (
              <p className={styles.panelEmpty}>No alerts. Rules raise them here, with the reason.</p>
            ) : (
              <ul className={styles.alertList}>
                {/* Every alert explains why it appeared and opens the record
                    that caused it (SVX-PRD-001 section 7.2). */}
                {data.alerts.map((alert) => (
                  <AlertItem key={alert.id} alert={alert} workspaceId={session.workspace.id} />
                ))}
              </ul>
            )}
          </div>
        </aside>
      </div>

      {completing ? (
        <CompleteTaskDialog
          task={completing}
          workspaceId={session.workspace.id}
          timeZone={data.workspace_time_zone}
          onClose={() => setCompleting(null)}
        />
      ) : null}
      {rescheduling ? (
        <RescheduleDialog
          task={rescheduling}
          workspaceId={session.workspace.id}
          onClose={() => setRescheduling(null)}
        />
      ) : null}
      {creating ? <NewTaskDialog session={session} onClose={() => setCreating(false)} /> : null}
    </div>
  )
}

type Tone = 'danger' | 'warning' | 'info' | 'neutral'

function TaskSection({
  id,
  title,
  tone,
  tasks,
  onComplete,
  onReschedule,
}: {
  id: string
  title: string
  tone: Tone
  tasks: Task[]
  onComplete: (task: Task) => void
  onReschedule: (task: Task) => void
}) {
  return (
    <section aria-labelledby={`${id}-heading`} className={`${ui.card} ${ui.cardFlush}`}>
      <div className={styles.panelHeader}>
        <h2 id={`${id}-heading`}>
          <span className={`${styles.dot} ${styles[tone]}`} aria-hidden="true" />
          {title}
          {/* The count is part of the heading so a screen reader announces it
              with the section, rather than as a stray number. */}
          <span className={styles.count}> ({tasks.length})</span>
        </h2>
      </div>
      {tasks.length === 0 ? (
        <p className={styles.panelEmpty}>Nothing overdue. Well kept.</p>
      ) : (
        <ul className={styles.list}>
          {tasks.map((task) => (
            <TaskRow key={task.id} task={task} onComplete={onComplete} onReschedule={onReschedule} />
          ))}
        </ul>
      )}
    </section>
  )
}
