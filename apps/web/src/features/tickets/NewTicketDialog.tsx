import { useState } from 'react'

import { TextArea, TextInput } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import { MemberSelect } from '@/components/MemberSelect'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { useClients, useCreateTicket } from '@/lib/queries'
import type { SessionContext, Ticket } from '@/lib/types'

/**
 * Raise a ticket (CRM09).
 *
 * Origin is asked for explicitly: a client's request and an issue we found
 * ourselves are reported differently, and guessing would blur the two.
 */
export function NewTicketDialog({
  session,
  onClose,
  onCreated,
}: {
  session: SessionContext
  onClose: () => void
  onCreated: (ticket: Ticket) => void
}) {
  const mutation = useCreateTicket(session.workspace.id)
  const { data: clients } = useClients(session.workspace.id)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [origin, setOrigin] = useState<'client_request' | 'internal_issue'>(
    'client_request',
  )
  const [priority, setPriority] = useState('normal')
  const [client, setClient] = useState('')
  const [owner, setOwner] = useState(session.user_id)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      { title, description, origin, priority, client, ownerId: owner },
      { onSuccess: onCreated },
    )
  }

  return (
    <Dialog title="New ticket" onClose={onClose}>
      <form onSubmit={submit} className={ui.form}>
        <label className={ui.field}>
          <span>Title</span>
          <TextInput
            limit={LIMITS.title}
            type="text"
            required
            value={title}
            onChange={setTitle}
          />
        </label>
        <label className={ui.field}>
          <span>Description</span>
          <TextArea
            limit={LIMITS.longText}
            rows={4}
            value={description}
            onChange={setDescription}
          />
        </label>
        <fieldset className={ui.fieldset}>
          <legend>Where did this come from?</legend>
          <label className={ui.radio}>
            <input
              type="radio"
              name="origin"
              checked={origin === 'client_request'}
              onChange={() => setOrigin('client_request')}
            />
            <span>The client asked for it</span>
          </label>
          <label className={ui.radio}>
            <input
              type="radio"
              name="origin"
              checked={origin === 'internal_issue'}
              onChange={() => setOrigin('internal_issue')}
            />
            <span>We found it ourselves</span>
          </label>
        </fieldset>
        <div className={ui.formGrid}>
          <label className={ui.field}>
            <span>Priority</span>
            <select value={priority} onChange={(event) => setPriority(event.target.value)}>
              <option value="low">Low</option>
              <option value="normal">Normal</option>
              <option value="high">High</option>
              <option value="critical">Critical — notifies the incident owner</option>
            </select>
          </label>
          <label className={ui.field}>
            <span>Client</span>
            <select value={client} onChange={(event) => setClient(event.target.value)}>
              <option value="">None</option>
              {(clients?.results ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className={ui.field}>
            <span>Owner</span>
            <MemberSelect
              workspaceId={session.workspace.id}
              value={owner}
              onChange={setOwner}
              emptyLabel="Unassigned"
            />
          </label>
        </div>
        <ErrorNotice error={mutation.error} fallback="The ticket could not be created." />
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Creating…' : 'Create ticket'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
