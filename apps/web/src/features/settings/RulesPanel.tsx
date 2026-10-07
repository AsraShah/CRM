import { useState } from 'react'

import { TextArea, TextInput } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import ui from '@/components/ui.module.css'
import {
  useInstallCatalogue,
  useRuleAction,
  useRules,
  useSimulateRule,
  useUpdateRule,
} from '@/lib/queries'
import { LIMITS } from '@/lib/limits'
import type { Rule, RuleAction, RuleDelay, SessionContext } from '@/lib/types'

const ACTION_LABELS: Record<RuleAction['type'], string> = {
  notify_owner: 'Alert the record owner',
  notify_manager: 'Alert the manager',
  create_task: 'Create a task for the owner',
  assign_owner: 'Assign an eligible owner',
}

/** The record type a rule's trigger is about, for simulation. */
function entityOf(trigger: string): string {
  return trigger.split('.')[0] ?? 'lead'
}

function describeDelay(delay: RuleDelay | null | undefined): string {
  if (!delay) return 'Immediately'
  if (delay.working_minutes) return `After ${delay.working_minutes} working minutes`
  if (delay.working_hours) return `After ${delay.working_hours} working hours`
  if (delay.working_days) return `After ${delay.working_days} working days`
  return 'Immediately'
}

/**
 * Rules (CRM06).
 *
 * Administrators work with the approved templates: timing, who is alerted, the
 * alert wording and the explanation shown with every alert. An edit writes a
 * new version and cancels work queued under the old one. Every rule starts
 * disabled; simulate it against a real record before switching it on.
 */
export function RulesPanel({ session }: { session: SessionContext }) {
  const { data: rules, isLoading, isError } = useRules(session.workspace.id)
  const install = useInstallCatalogue(session.workspace.id)
  const [editing, setEditing] = useState<Rule | null>(null)
  const [simulating, setSimulating] = useState<Rule | null>(null)

  return (
    <section aria-labelledby="rules-heading" className={`${ui.card} ${ui.cardFlush}`}>
      <div className={ui.panelHeader}>
        <h2 id="rules-heading" className={ui.sectionTitle}>
          Rules and alerts
        </h2>
        {rules && rules.length < 7 ? (
          <button
            type="button"
            className={`${ui.secondary} ${ui.small}`}
            disabled={install.isPending}
            onClick={() => install.mutate()}
          >
            {install.isPending ? 'Installing…' : 'Install the standard rules'}
          </button>
        ) : null}
      </div>
      <p className={ui.muted} style={{ margin: 0, padding: 'var(--space-3) var(--space-4)', fontSize: 'var(--text-sm)', borderBottom: '1px solid var(--colour-border)' }}>
        Rules create tasks and raise internal alerts. They never send anything
        outside the company, mark a deal won, accept work or act on a person.
      </p>
      <ErrorNotice error={install.error} fallback="The rules could not be installed." />

      {isLoading ? (
        <p role="status" className={ui.panelEmpty}>Loading rules…</p>
      ) : isError || !rules ? (
        <p role="alert" className={ui.panelEmpty}>Rules could not be loaded.</p>
      ) : rules.length === 0 ? (
        <p className={ui.panelEmpty}>No rules installed yet.</p>
      ) : (
        <ul className={ui.rows}>
          {rules.map((rule) => (
            <RuleCard
              key={rule.id}
              rule={rule}
              workspaceId={session.workspace.id}
              onEdit={() => setEditing(rule)}
              onSimulate={() => setSimulating(rule)}
            />
          ))}
        </ul>
      )}

      {editing ? (
        <EditRuleDialog
          rule={editing}
          workspaceId={session.workspace.id}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {simulating ? (
        <SimulateDialog rule={simulating} onClose={() => setSimulating(null)} />
      ) : null}
    </section>
  )
}

function RuleCard({
  rule,
  workspaceId,
  onEdit,
  onSimulate,
}: {
  rule: Rule
  workspaceId: string
  onEdit: () => void
  onSimulate: () => void
}) {
  const mutation = useRuleAction(workspaceId)
  const state = !rule.enabled ? 'Off' : rule.paused ? 'Paused' : 'On'

  function run(action: 'enable' | 'disable' | 'pause' | 'resume') {
    mutation.mutate({ ruleId: rule.id, action, expectedVersion: rule.version })
  }

  return (
    <li style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      <div className={ui.cardHeader}>
        <h3 style={{ fontSize: 'var(--text-base)' }}>{rule.name}</h3>
        <span
          className={state === 'On' ? ui.pillSuccess : state === 'Paused' ? ui.pillWarning : ui.pill}
        >
          {state} · v{rule.rule_version}
        </span>
      </div>
      <p className={ui.muted} style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
        {rule.explanation}
      </p>
      <dl className={ui.facts} style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
        <dt>When</dt>
        <dd>
          On {rule.trigger.replace('.', ' ').replace('_', ' ')}, {describeDelay(rule.delay).toLowerCase()}
        </dd>
        <dt>Does</dt>
        <dd>{rule.actions.map((a) => ACTION_LABELS[a.type]).join('; ')}</dd>
      </dl>
      <ErrorNotice error={mutation.error} fallback="The rule could not be changed." />
      <div className={ui.actions}>
        {!rule.enabled ? (
          <button type="button" className={`${ui.primary} ${ui.small}`} disabled={mutation.isPending} onClick={() => run('enable')}>
            Turn on
          </button>
        ) : rule.paused ? (
          <button type="button" className={`${ui.primary} ${ui.small}`} disabled={mutation.isPending} onClick={() => run('resume')}>
            Resume
          </button>
        ) : (
          <button type="button" className={`${ui.secondary} ${ui.small}`} disabled={mutation.isPending} onClick={() => run('pause')}>
            Pause
          </button>
        )}
        {rule.enabled ? (
          <button type="button" className={`${ui.secondary} ${ui.small}`} disabled={mutation.isPending} onClick={() => run('disable')}>
            Turn off
          </button>
        ) : null}
        <button type="button" className={`${ui.secondary} ${ui.small}`} onClick={onSimulate}>
          Simulate
        </button>
        <button type="button" className={`${ui.secondary} ${ui.small}`} onClick={onEdit}>
          Edit
        </button>
      </div>
    </li>
  )
}

type Unit = 'working_minutes' | 'working_hours' | 'working_days'

/** The server's ceiling for each unit (automation/schemas.py, Delay). */
const DELAY_MAX: Record<Unit, number> = {
  working_minutes: 60 * 24 * 30,
  working_hours: 24 * 30,
  working_days: 90,
}

function EditRuleDialog({
  rule,
  workspaceId,
  onClose,
}: {
  rule: Rule
  workspaceId: string
  onClose: () => void
}) {
  const mutation = useUpdateRule(workspaceId)
  const initialUnit: Unit = rule.delay?.working_days
    ? 'working_days'
    : rule.delay?.working_hours
      ? 'working_hours'
      : 'working_minutes'
  const [unit, setUnit] = useState<Unit>(initialUnit)
  const [amount, setAmount] = useState(String(rule.delay?.[initialUnit] ?? 0))
  const [actions, setActions] = useState<RuleAction[]>(rule.actions)
  const [explanation, setExplanation] = useState(rule.explanation)

  function setAction(index: number, patch: Partial<RuleAction>) {
    setActions((current) => current.map((a, i) => (i === index ? { ...a, ...patch } : a)))
  }

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const minutes = Number(amount)
    mutation.mutate(
      {
        ruleId: rule.id,
        expectedVersion: rule.version,
        changes: {
          delay: minutes > 0 ? { [unit]: minutes } : null,
          actions,
          explanation,
        },
      },
      { onSuccess: onClose },
    )
  }

  return (
    <Dialog title={`Edit ${rule.name}`} context="Saving writes a new version." onClose={onClose}>
      <form onSubmit={submit} className={ui.form}>
        <fieldset className={ui.fieldset}>
          <legend>Timing</legend>
          <div className={ui.formGrid}>
            <label className={ui.field}>
              <span>Wait</span>
              <input
                type="number"
                min="0"
                step="1"
                max={DELAY_MAX[unit]}
                required
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
              />
            </label>
            <label className={ui.field}>
              <span>Unit</span>
              <select value={unit} onChange={(event) => setUnit(event.target.value as Unit)}>
                <option value="working_minutes">Working minutes</option>
                <option value="working_hours">Working hours</option>
                <option value="working_days">Working days</option>
              </select>
            </label>
          </div>
          <p className={ui.hint} style={{ margin: 0 }}>
            Counted in the workspace's working time, so evenings, weekends and
            holidays are skipped. Zero acts immediately.
          </p>
        </fieldset>

        {actions.map((action, index) => (
          <fieldset key={index} className={ui.fieldset}>
            <legend>Action {index + 1}</legend>
            {action.type === 'notify_owner' || action.type === 'notify_manager' ? (
              <label className={ui.field}>
                <span>Who is alerted?</span>
                <select
                  value={action.type}
                  onChange={(event) =>
                    setAction(index, { type: event.target.value as RuleAction['type'] })
                  }
                >
                  <option value="notify_owner">The record owner</option>
                  <option value="notify_manager">The manager</option>
                </select>
              </label>
            ) : (
              <p className={ui.muted} style={{ margin: 0 }}>
                {ACTION_LABELS[action.type]}
              </p>
            )}
            <label className={ui.field}>
              <span>{action.type === 'create_task' ? 'Task title' : 'Alert title'}</span>
              <TextInput
                limit={LIMITS.title}
                value={action.title ?? ''}
                onChange={(value) => setAction(index, { title: value })}
              />
            </label>
          </fieldset>
        ))}

        <label className={ui.field}>
          <span>Explanation</span>
          <span className={ui.hint}>Shown with every alert this rule raises.</span>
          <TextArea
            limit={LIMITS.reason}
            rows={3}
            required
            value={explanation}
            onChange={setExplanation}
          />
        </label>
        <ErrorNotice error={mutation.error} fallback="The rule could not be saved." />
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Save new version'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function SimulateDialog({ rule, onClose }: { rule: Rule; onClose: () => void }) {
  const mutation = useSimulateRule()
  const entityType = entityOf(rule.trigger)
  const [entityId, setEntityId] = useState('')

  return (
    <Dialog title={`Simulate ${rule.name}`} context="Nothing is created or sent." onClose={onClose}>
      <form
        className={ui.form}
        onSubmit={(event) => {
          event.preventDefault()
          mutation.mutate({ ruleId: rule.id, entityType, entityId: entityId.trim() })
        }}
      >
        <label className={ui.field}>
          <span>Identifier of a {entityType}</span>
          <span className={ui.hint}>
            Copy it from the record. The rule is evaluated against its current state.
          </span>
          <TextInput
            limit={36}
            type="text"
            required
            pattern="[0-9a-fA-F-]{36}"
            value={entityId}
            onChange={setEntityId}
          />
        </label>
        <ErrorNotice error={mutation.error} fallback="The simulation could not run." />
        {mutation.data ? (
          <div className={mutation.data.would_fire ? ui.warning : ui.notice} role="status">
            <strong>
              {mutation.data.would_fire
                ? `Would act: ${mutation.data.proposed_actions.join(', ')}`
                : 'Would not act on this record.'}
            </strong>
            <ul style={{ margin: 'var(--space-2) 0 0', paddingLeft: 'var(--space-5)' }}>
              {mutation.data.conditions.map((c, i) => (
                <li key={i}>
                  {c.field} {c.op} {c.value === null ? '' : JSON.stringify(c.value)} — actual{' '}
                  {JSON.stringify(c.actual)}: {c.matched ? 'matches' : 'does not match'}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Close
          </button>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Running…' : 'Run simulation'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
