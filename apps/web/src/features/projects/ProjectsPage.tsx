import { AlertTriangle, CalendarDays, Check, FolderKanban, Search } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext } from 'react-router'

import { MilestoneActions } from './MilestoneActions'
import { AddDependency, MilestoneTasks } from './MilestoneExtras'
import styles from './ProjectsPage.module.css'
import { ScopeChanges } from './ScopeChanges'
import { EmptyState, PageHeader, Progress, Stat } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { formatDate } from '@/lib/dates'
import { useProjects } from '@/lib/queries'
import type { Milestone, MilestoneStatus, Project, SessionContext } from '@/lib/types'

export const MILESTONE_LABELS: Record<MilestoneStatus, string> = {
  planned: 'Planned',
  ready: 'Ready',
  in_progress: 'In progress',
  blocked: 'Blocked',
  // "Submitted" is the employee's claim; "Accepted" is the reviewer's decision.
  // They are different words because they are different acts (CRM08).
  in_review: 'Submitted, awaiting review',
  accepted: 'Accepted',
  cancelled: 'Cancelled',
}

const PROJECT_LABELS: Record<Project['status'], string> = {
  planned: 'Planned',
  in_progress: 'In progress',
  blocked: 'Blocked',
  completed: 'Completed',
  cancelled: 'Cancelled',
}

/**
 * Projects and milestones (CRM08).
 *
 * The screen keeps three things apart that delivery tools usually merge:
 * - submitting work (anyone doing it) and accepting it (a reviewer);
 * - a dependency that is not met, which is explained, not just disabled;
 * - a scope change (new work) and a defect (work we already owed).
 */
export function ProjectsPage() {
  const session = useOutletContext<SessionContext>()
  const [activeOnly, setActiveOnly] = useState(true)
  const [query, setQuery] = useState('')
  const { data, isLoading, isError } = useProjects(session.workspace.id)

  const header = (
    <PageHeader
      eyebrow="Delivery"
      title="Projects"
      subtitle="Created when a won deal is converted. A project's status follows from its milestones."
    />
  )

  if (isLoading) {
    return (
      <div className={ui.page}>
        {header}
        <p role="status" aria-live="polite" className={ui.muted}>
          Loading projects…
        </p>
      </div>
    )
  }
  if (isError || !data) {
    return (
      <div className={ui.page}>
        {header}
        <p role="alert" className={ui.error}>
          Projects could not be loaded.
        </p>
      </div>
    )
  }

  const active = data.results.filter((p) => p.status !== 'completed' && p.status !== 'cancelled')
  const needle = query.trim().toLowerCase()
  const projects = (activeOnly ? active : data.results).filter(
    (p) =>
      !needle ||
      p.name.toLowerCase().includes(needle) ||
      p.client_name.toLowerCase().includes(needle),
  )
  const liveMilestones = active.flatMap((p) => p.milestones)
  const inReview = liveMilestones.filter((m) => m.status === 'in_review').length
  const blocked = liveMilestones.filter((m) => m.status === 'blocked').length
  const overdue = liveMilestones.filter((m) => m.is_overdue).length

  return (
    <div className={ui.page}>
      {header}

      <div className={ui.stats}>
        <Stat feature label="Active projects" value={active.length} meta="Planned or in progress" />
        <Stat label="Awaiting review" value={inReview} meta="Submitted milestones" />
        <Stat label="Blocked" value={blocked} meta="Milestones waiting on something" />
        <Stat label="Overdue" value={overdue} meta="Milestones past their due date" />
      </div>

      <div className={ui.filterBar}>
        <label className={ui.search}>
          <Search size={16} aria-hidden="true" />
          <span className="visually-hidden">Search projects</span>
          <input
            type="search"
            maxLength={100}
            placeholder="Search by project or client"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className={ui.toggle}>
          <input
            type="checkbox"
            checked={activeOnly}
            onChange={(event) => setActiveOnly(event.target.checked)}
          />
          <span>Active projects only</span>
        </label>
        <span className={ui.filterCount}>
          {projects.length} {projects.length === 1 ? 'project' : 'projects'}
        </span>
      </div>

      {projects.length === 0 ? (
        <div className={ui.card}>
          <EmptyState
            icon={<FolderKanban size={22} aria-hidden="true" />}
            title={needle ? 'No projects match' : activeOnly ? 'No active projects' : 'No projects yet'}
          >
            {needle
              ? 'Try a different search.'
              : 'A project and its onboarding milestones are created when a deal is closed as won.'}
          </EmptyState>
        </div>
      ) : (
        <ul className={styles.projects}>
          {projects.map((project) => (
            <ProjectCard key={project.id} project={project} session={session} />
          ))}
        </ul>
      )}
    </div>
  )
}

function ProjectCard({ project, session }: { project: Project; session: SessionContext }) {
  const [showScope, setShowScope] = useState(false)
  const milestones = [...project.milestones].sort((a, b) => a.sequence - b.sequence)
  const accepted = milestones.filter((m) => m.status === 'accepted').length
  const counted = milestones.filter((m) => m.status !== 'cancelled').length

  return (
    <li className={`${ui.card} ${ui.cardFlush}`}>
      <div className={styles.projectHeader}>
        <div className={styles.projectTitle}>
          <h2>{project.name}</h2>
          <p className={ui.meta}>
            <span>{project.client_name}</span>
            {project.due_on ? (
              <span>
                <CalendarDays size={13} aria-hidden="true" />
                Due {formatDate(project.due_on)}
              </span>
            ) : null}
          </p>
        </div>
        <span className={projectPill(project.status)}>{PROJECT_LABELS[project.status]}</span>
      </div>

      <div className={styles.progressRow}>
        <Progress value={accepted} total={counted} label={`${project.name}: milestones accepted`} />
        <span className={styles.progressText}>
          {accepted} of {counted} milestones accepted
        </span>
      </div>

      {milestones.length === 0 ? (
        <p className={ui.panelEmpty}>This project has no milestones.</p>
      ) : (
        <ol className={styles.milestones}>
          {milestones.map((milestone, index) => (
            <MilestoneItem
              key={milestone.id}
              position={index + 1}
              milestone={milestone}
              siblings={milestones}
              session={session}
            />
          ))}
        </ol>
      )}

      <div className={styles.footer}>
        <button
          type="button"
          className={`${ui.ghost} ${ui.small}`}
          aria-expanded={showScope}
          onClick={() => setShowScope((value) => !value)}
        >
          {showScope ? 'Hide scope changes and defects' : 'Scope changes and defects'}
        </button>
        {showScope ? (
          <ScopeChanges project={project} milestones={milestones} session={session} />
        ) : null}
      </div>
    </li>
  )
}

function MilestoneItem({
  position,
  milestone,
  siblings,
  session,
}: {
  /** 1-based position in order. The stored sequence may start at 0. */
  position: number
  milestone: Milestone
  siblings: Milestone[]
  session: SessionContext
}) {
  const done = milestone.status === 'accepted'
  const closed = done || milestone.status === 'cancelled'
  const waiting = milestone.unmet_dependencies.length > 0 && !closed

  return (
    <li
      className={`${styles.milestone} ${milestone.is_overdue && !closed ? styles.milestoneOverdue : ''}`}
    >
      <div className={`${styles.marker} ${styles[`marker_${milestone.status}`] ?? ''}`} aria-hidden="true">
        {done ? <Check size={14} strokeWidth={3} /> : position}
      </div>

      <div className={styles.milestoneBody}>
        <div className={styles.milestoneRow}>
          <div className={styles.milestoneMain}>
            <p className={styles.milestoneName}>
              <span className="visually-hidden">{position}. </span>
              <span data-milestone-name>{milestone.name}</span>
            </p>
            <p className={ui.meta}>
              <span className={milestonePill(milestone.status)}>
                {MILESTONE_LABELS[milestone.status]}
              </span>
              {milestone.is_overdue && !closed ? (
                <span className={ui.pillDanger}>Overdue</span>
              ) : null}
              <span>{milestone.owner_email ?? 'No owner'}</span>
              <span>
                <CalendarDays size={13} aria-hidden="true" />
                {formatDate(milestone.due_on)}
              </span>
            </p>
          </div>
          <MilestoneActions milestone={milestone} session={session} />
        </div>

        {waiting ? (
          <p className={styles.callout}>
            <AlertTriangle size={14} aria-hidden="true" />
            <span>
              Waiting on{' '}
              {milestone.unmet_dependencies
                .map((dep) => `${dep.name} (${MILESTONE_LABELS[dep.status]})`)
                .join(', ')}
              . It cannot be accepted until these are, unless a manager records an override.
            </span>
          </p>
        ) : null}
        {milestone.status === 'blocked' && milestone.blocked_reason ? (
          <p className={styles.callout}>
            <AlertTriangle size={14} aria-hidden="true" />
            <span>Blocked by: {milestone.blocked_reason}</span>
          </p>
        ) : null}

        <details className={`${ui.disclosure} ${styles.details}`}>
          <summary>Details and tasks</summary>
          <div className={styles.detailsBody}>
            <dl className={ui.facts}>
              <dt>Owner</dt>
              <dd>{milestone.owner_email ?? <span className={ui.muted}>Not assigned</span>}</dd>
              <dt>Due</dt>
              <dd>{formatDate(milestone.due_on)}</dd>
              {milestone.evidence ? (
                <>
                  <dt>Evidence submitted</dt>
                  <dd>{milestone.evidence}</dd>
                </>
              ) : null}
              {milestone.accepted_at ? (
                <>
                  <dt>Accepted</dt>
                  <dd>
                    {formatDate(milestone.accepted_at)}
                    {milestone.acceptance_note ? ` — ${milestone.acceptance_note}` : ''}
                  </dd>
                </>
              ) : null}
              {milestone.dependency_override_reason ? (
                <>
                  <dt>Dependency overridden</dt>
                  <dd>{milestone.dependency_override_reason}</dd>
                </>
              ) : null}
              {milestone.status === 'cancelled' && milestone.cancelled_reason ? (
                <>
                  <dt>Cancelled because</dt>
                  <dd>{milestone.cancelled_reason}</dd>
                  <dt>Impact</dt>
                  <dd>{milestone.cancellation_impact}</dd>
                </>
              ) : null}
            </dl>
            <MilestoneTasks milestone={milestone} session={session} />
            <AddDependency milestone={milestone} siblings={siblings} session={session} />
          </div>
        </details>
      </div>
    </li>
  )
}

function projectPill(status: Project['status']): string | undefined {
  if (status === 'blocked') return ui.pillDanger
  if (status === 'completed') return ui.pillSuccess
  if (status === 'in_progress') return ui.pillInfo
  return ui.pill
}

export function milestonePill(status: MilestoneStatus): string | undefined {
  if (status === 'blocked') return ui.pillDanger
  if (status === 'in_review') return ui.pillWarning
  if (status === 'accepted') return ui.pillSuccess
  if (status === 'in_progress') return ui.pillInfo
  return ui.pill
}
