import { useState } from 'react'

import { AiAssist } from './AiAssist'
import { TextArea, TextInput } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import { MemberSelect } from '@/components/MemberSelect'
import ui from '@/components/ui.module.css'
import { LIMITS, MONEY_MAX, validators } from '@/lib/limits'
import { formatDateTime, localDateTimeFromNow, localToIso } from '@/lib/dates'
import {
  useActivities,
  useAssignLead,
  useContact,
  useCorrectActivity,
  useCreateOpportunity,
  useDuplicates,
  useLeadStatus,
  useMergeContact,
  useRecordActivity,
  useUpdateContact,
  useUpdateLead,
} from '@/lib/queries'
import type { Contact, Lead, LeadStatus, SessionContext } from '@/lib/types'

const STATUS_LABELS: Record<LeadStatus, string> = {
  new: 'New',
  assigned: 'Assigned',
  contacting: 'Contacting',
  qualified: 'Qualified',
  nurture: 'Nurture',
  disqualified: 'Disqualified',
}

const ACTIVITY_LABELS: Record<string, string> = {
  call: 'Call',
  email: 'Email',
  meeting: 'Meeting',
  linkedin: 'LinkedIn touch',
  note: 'Note',
}

type Section = 'activity' | 'details' | 'status' | 'assign' | 'deal'

/**
 * Everything a salesperson does to one lead (CRM03, CRM04).
 *
 * Opened from the lead list so the list stays a list. Each section is offered
 * only to roles the server would allow to use it.
 */
export function LeadDialog({
  lead,
  session,
  onClose,
}: {
  lead: Lead
  session: SessionContext
  onClose: () => void
}) {
  const can = (permission: string) => session.permissions.includes(permission)
  const sections: { key: Section; label: string }[] = []
  if (can('activity.record')) sections.push({ key: 'activity', label: 'Activity' })
  if (can('lead.manage')) sections.push({ key: 'details', label: 'Details' })
  if (can('lead.manage')) sections.push({ key: 'status', label: 'Status' })
  if (can('lead.reassign')) sections.push({ key: 'assign', label: 'Owner' })
  if (can('opportunity.manage')) sections.push({ key: 'deal', label: 'Open a deal' })

  const [section, setSection] = useState<Section>(sections[0]?.key ?? 'activity')

  return (
    <Dialog
      title={lead.contact_name}
      context={`${STATUS_LABELS[lead.status]} · ${lead.owner_email ?? 'Unassigned'}`}
      onClose={onClose}
    >
      {sections.length > 1 ? (
        <div className={ui.actions} role="group" aria-label="Section" style={{ marginBottom: 'var(--space-4)' }}>
          {sections.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              className={section === key ? ui.primary : ui.secondary}
              aria-pressed={section === key}
              onClick={() => setSection(key)}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}

      {section === 'activity' ? <ActivitySection lead={lead} session={session} /> : null}
      {section === 'details' ? (
        <DetailsSection lead={lead} session={session} onDone={onClose} />
      ) : null}
      {section === 'status' ? (
        <StatusSection lead={lead} session={session} onDone={onClose} />
      ) : null}
      {section === 'assign' ? (
        <AssignSection lead={lead} session={session} onDone={onClose} />
      ) : null}
      {section === 'deal' ? (
        <DealSection lead={lead} session={session} onDone={onClose} />
      ) : null}
    </Dialog>
  )
}

function ActivitySection({ lead, session }: { lead: Lead; session: SessionContext }) {
  const { data, isLoading } = useActivities(session.workspace.id, { contact: lead.contact })
  const mutation = useRecordActivity(session.workspace.id)
  const [kind, setKind] = useState('call')
  const [occurredAt, setOccurredAt] = useState(nowLocal())
  const [outcome, setOutcome] = useState('')
  const [scheduleFollowUp, setScheduleFollowUp] = useState(true)
  const [followUpTitle, setFollowUpTitle] = useState('')
  const [followUpDue, setFollowUpDue] = useState(localDateTimeFromNow(1))
  const [correcting, setCorrecting] = useState<string | null>(null)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        contact: lead.contact,
        lead: lead.id,
        kind,
        occurredAt: localToIso(occurredAt),
        outcome,
        ...(scheduleFollowUp
          ? { followUpTitle, followUpDueAt: localToIso(followUpDue) }
          : {}),
      },
      {
        onSuccess: () => {
          setOutcome('')
          setFollowUpTitle('')
          setOccurredAt(nowLocal())
        },
      },
    )
  }

  return (
    <div className={ui.form}>
      <form onSubmit={submit} className={ui.form}>
        <div className={ui.formGrid}>
          <label className={ui.field}>
            <span>What was it?</span>
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              {Object.entries(ACTIVITY_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className={ui.field}>
            <span>When</span>
            <input
              type="datetime-local"
              required
              max={nowLocal()}
              value={occurredAt}
              onChange={(event) => setOccurredAt(event.target.value)}
            />
          </label>
        </div>
        <label className={ui.field}>
          <span>Outcome</span>
          <TextArea
            limit={LIMITS.longText}
            rows={2}
            value={outcome}
            onChange={setOutcome}
          />
        </label>
        <fieldset className={ui.fieldset}>
          <legend>Next action</legend>
          <label className={ui.checkbox}>
            <input
              type="checkbox"
              checked={scheduleFollowUp}
              onChange={(event) => setScheduleFollowUp(event.target.checked)}
            />
            <span>Schedule a follow-up</span>
          </label>
          {scheduleFollowUp ? (
            <div className={ui.formGrid}>
              <label className={ui.field}>
                <span>Follow-up</span>
                <TextInput
                  limit={LIMITS.title}
                  type="text"
                  required
                  placeholder="Send pricing"
                  value={followUpTitle}
                  onChange={setFollowUpTitle}
                />
              </label>
              <label className={ui.field}>
                <span>Due</span>
                <input
                  type="datetime-local"
                  required
                  value={followUpDue}
                  onChange={(event) => setFollowUpDue(event.target.value)}
                />
              </label>
            </div>
          ) : null}
        </fieldset>
        <p className={ui.hint} style={{ margin: 0 }}>
          Recorded as self-reported. Only an activity a provider confirms is
          labelled as verified.
        </p>
        <ErrorNotice error={mutation.error} fallback="The activity could not be recorded." />
        <div className={ui.actions}>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Record activity'}
          </button>
        </div>
      </form>

      <h3 style={{ margin: 'var(--space-2) 0 0', fontSize: 'var(--text-base)' }}>
        History with {lead.contact_name}
      </h3>
      <AiAssist activities={data?.results ?? []} session={session} />
      {isLoading ? (
        <p role="status">Loading…</p>
      ) : (data?.results ?? []).length === 0 ? (
        <p className={ui.muted}>Nothing recorded yet.</p>
      ) : (
        <ul className={ui.list}>
          {(data?.results ?? []).map((activity) => (
            <li key={activity.id} className={ui.card} style={{ padding: 'var(--space-3)' }}>
              <div className={ui.cardHeader}>
                <strong>{ACTIVITY_LABELS[activity.kind] ?? activity.kind}</strong>
                {/* The two labels must never look alike (CRM04). */}
                {activity.evidence_type === 'provider_confirmed' ? (
                  <span className={ui.pillSuccess}>Provider-confirmed</span>
                ) : (
                  <span className={ui.pill}>Self-reported</span>
                )}
              </div>
              <p className={ui.muted} style={{ margin: 'var(--space-1) 0 0' }}>
                {formatDateTime(activity.occurred_at)}
                {activity.author_email ? ` · ${activity.author_email}` : ''}
                {activity.corrects ? ' · correction of an earlier entry' : ''}
              </p>
              {activity.outcome ? (
                <p style={{ margin: 'var(--space-1) 0 0', whiteSpace: 'pre-wrap' }}>
                  {activity.outcome}
                </p>
              ) : null}
              {activity.follow_up_task ? (
                <p className={ui.hint} style={{ margin: 'var(--space-1) 0 0' }}>
                  Follow-up scheduled on Today.
                </p>
              ) : null}
              {/* An employee corrects their own record by appending, never by
                  editing the original (CRM04, CRM10). */}
              {(activity.author === session.user_id ||
                session.permissions.includes('exception.review')) &&
              activity.evidence_type === 'self_reported' ? (
                correcting === activity.id ? (
                  <CorrectionForm
                    activityId={activity.id}
                    original={activity.outcome}
                    workspaceId={session.workspace.id}
                    onDone={() => setCorrecting(null)}
                  />
                ) : (
                  <button
                    type="button"
                    className={ui.secondary}
                    style={{ marginTop: 'var(--space-2)' }}
                    aria-label={`Correct this ${ACTIVITY_LABELS[activity.kind] ?? 'entry'}`}
                    onClick={() => setCorrecting(activity.id)}
                  >
                    Correct
                  </button>
                )
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Append a correction to an activity; the original stays in the history. */
function CorrectionForm({
  activityId,
  original,
  workspaceId,
  onDone,
}: {
  activityId: string
  original: string
  workspaceId: string
  onDone: () => void
}) {
  const mutation = useCorrectActivity(workspaceId)
  const [outcome, setOutcome] = useState(original)
  const [reason, setReason] = useState('')
  return (
    <form
      className={ui.form}
      style={{ marginTop: 'var(--space-2)' }}
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate({ activityId, outcome, reason }, { onSuccess: onDone })
      }}
    >
      <label className={ui.field}>
        <span>Corrected outcome</span>
        <TextArea limit={LIMITS.longText} rows={2} required value={outcome} onChange={setOutcome} />
      </label>
      <label className={ui.field}>
        <span>Why the correction?</span>
        <TextInput limit={LIMITS.shortReason} type="text" required value={reason} onChange={setReason} />
      </label>
      <p className={ui.hint} style={{ margin: 0 }}>
        The original entry is kept; this is added as a linked correction.
      </p>
      <ErrorNotice error={mutation.error} fallback="The correction could not be saved." />
      <div className={ui.actions}>
        <button type="submit" className={ui.primary} disabled={mutation.isPending}>
          {mutation.isPending ? 'Saving…' : 'Save correction'}
        </button>
        <button type="button" className={ui.secondary} onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  )
}

/**
 * Correct the person's details and the lead's notes (CRM03).
 *
 * Contact and lead are saved separately because they are separate records with
 * separate versions; only the ones that changed are sent.
 */
function DetailsSection({
  lead,
  session,
  onDone,
}: {
  lead: Lead
  session: SessionContext
  onDone: () => void
}) {
  const { data: contact, isLoading } = useContact(session.workspace.id, lead.contact)
  if (isLoading) return <p role="status">Loading…</p>
  if (!contact) return <p role="alert">The contact could not be loaded.</p>
  return (
    <div className={ui.form}>
      <DetailsForm lead={lead} contact={contact} session={session} onDone={onDone} />
      <Duplicates contact={contact} session={session} />
    </div>
  )
}

function DetailsForm({
  lead,
  contact,
  session,
  onDone,
}: {
  lead: Lead
  contact: Contact
  session: SessionContext
  onDone: () => void
}) {
  const updateContact = useUpdateContact(session.workspace.id)
  const updateLead = useUpdateLead(session.workspace.id)
  const canEditContact = session.permissions.includes('contact.manage')

  const [name, setName] = useState(contact.display_name)
  const [email, setEmail] = useState(contact.email)
  const [phone, setPhone] = useState(contact.phone)
  const [jobTitle, setJobTitle] = useState(contact.job_title)
  const [notes, setNotes] = useState(lead.notes)
  const [need, setNeed] = useState(lead.need)
  const [fit, setFit] = useState(lead.fit)

  const contactChanges = changed(
    { display_name: contact.display_name, email: contact.email, phone: contact.phone, job_title: contact.job_title },
    { display_name: name, email, phone, job_title: jobTitle },
  )
  const leadChanges = changed({ notes: lead.notes, need: lead.need, fit: lead.fit }, { notes, need, fit })
  const nothingChanged =
    Object.keys(contactChanges).length === 0 && Object.keys(leadChanges).length === 0
  const pending = updateContact.isPending || updateLead.isPending

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    try {
      if (Object.keys(contactChanges).length > 0) {
        await updateContact.mutateAsync({
          contactId: contact.id,
          expectedVersion: contact.version,
          changes: contactChanges,
        })
      }
      if (Object.keys(leadChanges).length > 0) {
        await updateLead.mutateAsync({
          leadId: lead.id,
          expectedVersion: lead.version,
          changes: leadChanges,
        })
      }
      onDone()
    } catch {
      // Shown by the ErrorNotice below; what was typed stays in the form.
    }
  }

  const qualified = lead.status === 'qualified'

  return (
    <form onSubmit={(event) => void submit(event)} className={ui.form}>
      <fieldset className={ui.fieldset} disabled={!canEditContact}>
        <legend>Person</legend>
        <label className={ui.field}>
          <span>Name</span>
          <TextInput limit={LIMITS.name} type="text" required value={name} onChange={setName} />
        </label>
        <div className={ui.formGrid}>
          <label className={ui.field}>
            <span>Email</span>
            <TextInput limit={LIMITS.email} type="email" value={email} onChange={setEmail} />
          </label>
          <label className={ui.field}>
            <span>Phone</span>
            <TextInput
              limit={LIMITS.phone}
              validate={validators.phone}
              type="tel"
              value={phone}
              onChange={setPhone}
            />
          </label>
          <label className={ui.field}>
            <span>Job title</span>
            <TextInput limit={LIMITS.jobTitle} type="text" value={jobTitle} onChange={setJobTitle} />
          </label>
        </div>
      </fieldset>
      <label className={ui.field}>
        <span>Need{qualified ? '' : ' (optional until qualified)'}</span>
        <TextArea limit={LIMITS.reason} rows={2} required={qualified} value={need} onChange={setNeed} />
      </label>
      <label className={ui.field}>
        <span>Fit{qualified ? '' : ' (optional until qualified)'}</span>
        <TextArea limit={LIMITS.reason} rows={2} required={qualified} value={fit} onChange={setFit} />
      </label>
      <label className={ui.field}>
        <span>Notes</span>
        <TextArea limit={LIMITS.longText} rows={3} value={notes} onChange={setNotes} />
      </label>
      <ErrorNotice error={updateContact.error} fallback="The contact could not be saved." />
      <ErrorNotice error={updateLead.error} fallback="The lead could not be saved." />
      <div className={ui.actions}>
        <button type="submit" className={ui.primary} disabled={pending || nothingChanged}>
          {pending ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </form>
  )
}

/**
 * Possible duplicates of this person, and a reviewed merge (CRM02).
 *
 * Suggestions come from name similarity and are never merged automatically.
 * Merging moves the other record's history onto this one and keeps the other
 * as a retired record with its source reference.
 */
function Duplicates({ contact, session }: { contact: Contact; session: SessionContext }) {
  const canMerge = session.permissions.includes('contact.merge')
  const { data } = useDuplicates(session.workspace.id, contact.id, canMerge)
  const merge = useMergeContact(session.workspace.id)
  const [target, setTarget] = useState<string | null>(null)
  const [reason, setReason] = useState('')

  if (!canMerge || !data || data.length === 0) return null

  return (
    <fieldset className={ui.fieldset}>
      <legend>Possible duplicates</legend>
      <p className={ui.hint} style={{ margin: 0 }}>
        Similar names only. Check before merging: a merge moves the other
        record's leads, deals and history onto {contact.display_name}.
      </p>
      <ul className={ui.list}>
        {data.map((candidate) => (
          <li key={candidate.id}>
            <strong>{candidate.display_name}</strong>{' '}
            <span className={ui.muted}>
              {[candidate.email, candidate.phone].filter(Boolean).join(' · ') || 'no contact details'}
              {' · '}
              {candidate.reason}
            </span>
            {target === candidate.id ? (
              <form
                className={ui.form}
                onSubmit={(event) => {
                  event.preventDefault()
                  merge.mutate(
                    {
                      survivorId: contact.id,
                      duplicateId: candidate.id,
                      survivorVersion: contact.version,
                      duplicateVersion: candidate.version,
                      reason,
                    },
                    { onSuccess: () => setTarget(null) },
                  )
                }}
              >
                <label className={ui.field}>
                  <span>Why are these the same person?</span>
                  <TextInput limit={LIMITS.shortReason} type="text" required value={reason} onChange={setReason} />
                </label>
                <ErrorNotice error={merge.error} fallback="The contacts could not be merged." />
                <div className={ui.actions}>
                  <button type="submit" className={ui.danger} disabled={merge.isPending}>
                    {merge.isPending ? 'Merging…' : `Merge into ${contact.display_name}`}
                  </button>
                  <button type="button" className={ui.secondary} onClick={() => setTarget(null)}>
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <button
                type="button"
                className={ui.secondary}
                style={{ marginLeft: 'var(--space-2)' }}
                onClick={() => setTarget(candidate.id)}
              >
                Review merge
              </button>
            )}
          </li>
        ))}
      </ul>
    </fieldset>
  )
}

/** The fields whose value differs, so an unchanged field is never re-sent. */
function changed(before: Record<string, string>, after: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(after).filter(([key, value]) => value !== before[key]))
}

function StatusSection({
  lead,
  session,
  onDone,
}: {
  lead: Lead
  session: SessionContext
  onDone: () => void
}) {
  const mutation = useLeadStatus(session.workspace.id)
  const [status, setStatus] = useState<LeadStatus>(lead.status)
  const [reviewAt, setReviewAt] = useState(localDateTimeFromNow(30))
  const [reason, setReason] = useState('')
  const [need, setNeed] = useState(lead.need)
  const [fit, setFit] = useState(lead.fit)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        leadId: lead.id,
        status,
        expectedVersion: lead.version,
        ...(status === 'nurture' ? { nurtureReviewAt: localToIso(reviewAt) } : {}),
        ...(status === 'disqualified' ? { disqualifiedReason: reason } : {}),
        ...(status === 'qualified' ? { need, fit } : {}),
      },
      { onSuccess: onDone },
    )
  }

  return (
    <form onSubmit={submit} className={ui.form}>
      <p className={ui.muted} style={{ margin: 0 }}>
        Status is about qualification. A deal's progress is tracked separately,
        on the pipeline.
      </p>
      <label className={ui.field}>
        <span>Status</span>
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value as LeadStatus)}
        >
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {status === 'nurture' ? (
        <label className={ui.field}>
          <span>When should someone look at it again?</span>
          <input
            type="datetime-local"
            required
            value={reviewAt}
            onChange={(event) => setReviewAt(event.target.value)}
          />
        </label>
      ) : null}
      {status === 'qualified' ? (
        <>
          <label className={ui.field}>
            <span>Need</span>
            <span className={ui.hint}>What problem do they need solved?</span>
            <TextArea limit={LIMITS.reason} rows={2} required value={need} onChange={setNeed} />
          </label>
          <label className={ui.field}>
            <span>Fit</span>
            <span className={ui.hint}>Why is it a fit for what we sell?</span>
            <TextArea limit={LIMITS.reason} rows={2} required value={fit} onChange={setFit} />
          </label>
        </>
      ) : null}
      {status === 'disqualified' ? (
        <label className={ui.field}>
          <span>Why is it disqualified?</span>
          <TextArea
            limit={LIMITS.reason}
            rows={2}
            required
            value={reason}
            onChange={setReason}
          />
        </label>
      ) : null}
      <ErrorNotice error={mutation.error} fallback="The status could not be changed." />
      <div className={ui.actions}>
        <button
          type="submit"
          className={ui.primary}
          disabled={mutation.isPending || status === lead.status}
        >
          {mutation.isPending ? 'Saving…' : 'Change status'}
        </button>
      </div>
    </form>
  )
}

function AssignSection({
  lead,
  session,
  onDone,
}: {
  lead: Lead
  session: SessionContext
  onDone: () => void
}) {
  const mutation = useAssignLead(session.workspace.id)
  const [owner, setOwner] = useState(lead.owner ?? '')
  const [reason, setReason] = useState('')

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      { leadId: lead.id, owner, expectedVersion: lead.version, reason },
      { onSuccess: onDone },
    )
  }

  return (
    <form onSubmit={submit} className={ui.form}>
      <label className={ui.field}>
        <span>Owner</span>
        <MemberSelect
          workspaceId={session.workspace.id}
          value={owner}
          onChange={setOwner}
          required
        />
      </label>
      <label className={ui.field}>
        <span>Reason (optional)</span>
        <TextInput limit={LIMITS.reference} type="text" value={reason} onChange={setReason} />
      </label>
      <ErrorNotice error={mutation.error} fallback="The lead could not be reassigned." />
      <div className={ui.actions}>
        <button
          type="submit"
          className={ui.primary}
          disabled={mutation.isPending || owner === (lead.owner ?? '')}
        >
          {mutation.isPending ? 'Saving…' : lead.owner ? 'Reassign' : 'Assign'}
        </button>
      </div>
    </form>
  )
}

function DealSection({
  lead,
  session,
  onDone,
}: {
  lead: Lead
  session: SessionContext
  onDone: () => void
}) {
  const mutation = useCreateOpportunity(session.workspace.id)
  const [service, setService] = useState('')
  const [amount, setAmount] = useState('')
  const [currency, setCurrency] = useState(session.workspace.default_currency)
  const [closeOn, setCloseOn] = useState('')
  const [created, setCreated] = useState(false)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        contact: lead.contact,
        lead: lead.id,
        service,
        owner: lead.owner ?? session.user_id,
        amount,
        // A currency is sent only with an amount. Without a value, there is
        // nothing to denominate.
        currency: amount ? currency.toUpperCase() : '',
        expectedCloseOn: closeOn,
      },
      { onSuccess: () => setCreated(true) },
    )
  }

  if (created) {
    return (
      <div className={ui.form}>
        <p className={ui.success} role="status">
          The deal is on the pipeline at Discovery.
        </p>
        <div className={ui.actions}>
          <button type="button" className={ui.primary} onClick={onDone}>
            Done
          </button>
        </div>
      </div>
    )
  }

  return (
    <form onSubmit={submit} className={ui.form}>
      {lead.status !== 'qualified' ? (
        <p className={ui.warning}>
          This lead is not marked qualified. You can still open a deal, but
          consider qualifying it first.
        </p>
      ) : null}
      <label className={ui.field}>
        <span>What is being sold?</span>
        <TextInput
          limit={LIMITS.name}
          type="text"
          required
          value={service}
          onChange={setService}
        />
      </label>
      <div className={ui.formGrid}>
        <label className={ui.field}>
          <span>Value (optional)</span>
          <span className={ui.hint}>Leave blank if not yet agreed. It shows as unknown, not zero.</span>
          <input
            type="number"
            min="0"
            max={MONEY_MAX}
            step="0.01"
            inputMode="decimal"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </label>
        <label className={ui.field}>
          <span>Currency</span>
          <span className={ui.hint}>Three-letter code.</span>
          <TextInput
            limit={LIMITS.currency}
            validate={validators.currency}
            type="text"
            required={Boolean(amount)}
            value={currency}
            onChange={(value) => setCurrency(value.toUpperCase())}
          />
        </label>
        <label className={ui.field}>
          <span>Expected close (optional)</span>
          <input type="date" value={closeOn} onChange={(event) => setCloseOn(event.target.value)} />
        </label>
      </div>
      <ErrorNotice error={mutation.error} fallback="The deal could not be created." />
      <div className={ui.actions}>
        <button type="submit" className={ui.primary} disabled={mutation.isPending}>
          {mutation.isPending ? 'Saving…' : 'Open deal'}
        </button>
      </div>
    </form>
  )
}

function nowLocal(): string {
  const now = new Date()
  const offset = now.getTimezoneOffset() * 60_000
  return new Date(now.getTime() - offset).toISOString().slice(0, 16)
}
