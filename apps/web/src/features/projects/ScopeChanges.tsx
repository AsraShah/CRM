import { useState } from 'react'

import { TextArea, TextInput } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { formatDateTime } from '@/lib/dates'
import { useRecordScopeChange, useScopeChanges } from '@/lib/queries'
import type { Milestone, Project, SessionContext } from '@/lib/types'

/**
 * Scope changes and defects for one project (CRM08).
 *
 * The choice between the two is the first field and is required, because the
 * distinction decides who pays: a scope change is new work the client asked
 * for, a defect is work already owed.
 */
export function ScopeChanges({
  project,
  milestones,
  session,
}: {
  project: Project
  milestones: Milestone[]
  session: SessionContext
}) {
  const { data, isLoading, isError } = useScopeChanges(session.workspace.id, project.id)
  const record = useRecordScopeChange(session.workspace.id, project.id)
  const canRecord = session.permissions.includes('project.manage')

  const [kind, setKind] = useState<'scope_change' | 'defect' | ''>('')
  const [description, setDescription] = useState('')
  const [impact, setImpact] = useState('')
  const [milestoneId, setMilestoneId] = useState('')

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!kind) return
    record.mutate(
      { kind, description, commercialImpact: impact, milestoneId },
      {
        onSuccess: () => {
          setKind('')
          setDescription('')
          setImpact('')
          setMilestoneId('')
        },
      },
    )
  }

  return (
    <section
      aria-label={`Scope changes and defects for ${project.name}`}
      style={{ marginTop: 'var(--space-3)' }}
      className={ui.form}
    >
      {isLoading ? (
        <p role="status">Loading…</p>
      ) : isError ? (
        <p role="alert">Scope changes could not be loaded.</p>
      ) : (data ?? []).length === 0 ? (
        <p className={ui.muted}>Nothing recorded for this project.</p>
      ) : (
        <ul className={ui.list}>
          {(data ?? []).map((change) => (
            <li key={change.id} className={ui.card}>
              <span className={change.kind === 'defect' ? ui.pillDanger : ui.pillInfo}>
                {change.kind === 'defect' ? 'Defect' : 'Scope change'}
              </span>{' '}
              <span className={ui.muted}>{formatDateTime(change.recorded_at)}</span>
              <p style={{ margin: 'var(--space-2) 0 0', whiteSpace: 'pre-wrap' }}>
                {change.description}
              </p>
              {change.commercial_impact ? (
                <p className={ui.muted} style={{ margin: 'var(--space-1) 0 0' }}>
                  Commercial impact: {change.commercial_impact}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {canRecord ? (
        <form onSubmit={submit} className={ui.form}>
          <fieldset className={ui.fieldset}>
            <legend>Which is it?</legend>
            <label className={ui.radio}>
              <input
                type="radio"
                name={`kind-${project.id}`}
                required
                checked={kind === 'scope_change'}
                onChange={() => setKind('scope_change')}
              />
              <span>Scope change — new work the client has asked for</span>
            </label>
            <label className={ui.radio}>
              <input
                type="radio"
                name={`kind-${project.id}`}
                checked={kind === 'defect'}
                onChange={() => setKind('defect')}
              />
              <span>Defect — work we already owed that is not right</span>
            </label>
          </fieldset>
          <label className={ui.field}>
            <span>Description</span>
            <TextArea
              limit={LIMITS.longText}
              rows={3}
              required
              value={description}
              onChange={setDescription}
            />
          </label>
          <div className={ui.formGrid}>
            <label className={ui.field}>
              <span>Commercial impact (optional)</span>
              <TextInput
                limit={LIMITS.reason}
                type="text"
                value={impact}
                onChange={setImpact}
              />
            </label>
            <label className={ui.field}>
              <span>Milestone (optional)</span>
              <select
                value={milestoneId}
                onChange={(event) => setMilestoneId(event.target.value)}
              >
                <option value="">The project as a whole</option>
                {milestones.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <ErrorNotice error={record.error} fallback="It could not be recorded." />
          <div className={ui.actions}>
            <button type="submit" className={ui.primary} disabled={record.isPending}>
              {record.isPending ? 'Saving…' : 'Record'}
            </button>
          </div>
        </form>
      ) : null}
    </section>
  )
}
