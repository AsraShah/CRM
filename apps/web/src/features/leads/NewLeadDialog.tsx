import { useState } from 'react'

import { TextArea, TextInput } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import { MemberSelect } from '@/components/MemberSelect'
import ui from '@/components/ui.module.css'
import { LIMITS, validators } from '@/lib/limits'
import { LeadAfterContactError, useCreateLead } from '@/lib/queries'
import type { SessionContext } from '@/lib/types'

export const SOURCE_LABELS: Record<string, string> = {
  referral: 'Referral',
  website: 'Website',
  linkedin: 'LinkedIn',
  outbound: 'Outbound prospecting',
  event: 'Event',
  import: 'Spreadsheet import',
  other: 'Other',
  unknown: 'Unknown',
}

/**
 * Add a lead by hand (CRM03).
 *
 * The person and the lead are created in two steps on the server. If the lead
 * step fails after the person was saved, the form keeps that person and the
 * retry only creates the lead — so a retry cannot leave a duplicate contact.
 */
export function NewLeadDialog({
  session,
  onClose,
}: {
  session: SessionContext
  onClose: () => void
}) {
  const mutation = useCreateLead(session.workspace.id)
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [jobTitle, setJobTitle] = useState('')
  const [source, setSource] = useState('referral')
  const [sourceReference, setSourceReference] = useState('')
  const [notes, setNotes] = useState('')
  const [owner, setOwner] = useState('')
  const [savedContactId, setSavedContactId] = useState<string | null>(null)

  // Assigning to someone else is a manager's call; anyone may take it themselves.
  const canAssignOthers = session.permissions.includes('lead.reassign')

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        displayName,
        email,
        phone,
        jobTitle,
        source,
        sourceReference,
        notes,
        owner,
        ...(savedContactId ? { existingContactId: savedContactId } : {}),
      },
      {
        onSuccess: onClose,
        onError: (error) => {
          if (error instanceof LeadAfterContactError) setSavedContactId(error.contactId)
        },
      },
    )
  }

  const error =
    mutation.error instanceof LeadAfterContactError ? mutation.error.cause : mutation.error

  return (
    <Dialog title="New lead" onClose={onClose}>
      <form onSubmit={submit} className={ui.form}>
        <fieldset className={ui.fieldset} disabled={savedContactId !== null}>
          <legend>Who is it?</legend>
          <label className={ui.field}>
            <span>Name</span>
            <TextInput
              limit={LIMITS.name}
              type="text"
              required
              value={displayName}
              onChange={setDisplayName}
            />
          </label>
          <div className={ui.formGrid}>
            <label className={ui.field}>
              <span>Email</span>
              <TextInput
                limit={LIMITS.email}
                type="email"
                value={email}
                onChange={setEmail}
              />
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
              <TextInput
                limit={LIMITS.jobTitle}
                type="text"
                value={jobTitle}
                onChange={setJobTitle}
              />
            </label>
          </div>
        </fieldset>

        {savedContactId ? (
          <p className={ui.notice}>
            {displayName} has been saved as a contact. Only the lead still needs
            creating — retrying will not add them twice.
          </p>
        ) : null}

        <div className={ui.formGrid}>
          <label className={ui.field}>
            <span>Source</span>
            <select value={source} onChange={(event) => setSource(event.target.value)}>
              {Object.entries(SOURCE_LABELS)
                .filter(([value]) => value !== 'import')
                .map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
            </select>
          </label>
          <label className={ui.field}>
            <span>Source detail (optional)</span>
            <TextInput
              limit={LIMITS.reference}
              type="text"
              placeholder="Who referred them, which event…"
              value={sourceReference}
              onChange={setSourceReference}
            />
          </label>
          <label className={ui.field}>
            <span>Owner</span>
            {canAssignOthers ? (
              <MemberSelect
                workspaceId={session.workspace.id}
                value={owner}
                onChange={setOwner}
                emptyLabel="Unassigned for now"
              />
            ) : (
              <select value={owner} onChange={(event) => setOwner(event.target.value)}>
                <option value="">Unassigned for now</option>
                <option value={session.user_id}>Me</option>
              </select>
            )}
          </label>
        </div>
        <label className={ui.field}>
          <span>Notes (optional)</span>
          <TextArea limit={LIMITS.longText} rows={2} value={notes} onChange={setNotes} />
        </label>

        <ErrorNotice error={error} fallback="The lead could not be created." />
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : savedContactId ? 'Retry creating the lead' : 'Create lead'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
