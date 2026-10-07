import { useState } from 'react'

import { TextArea, TextInput } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import { MemberSelect } from '@/components/MemberSelect'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { useConvertOpportunity } from '@/lib/queries'
import type { Opportunity } from '@/lib/types'

/** conversion.py accepts only these as a delivery owner. */
const DELIVERY_ROLES = ['owner', 'delivery_manager', 'delivery_employee'] as const

/**
 * Close a deal as won (CRM07).
 *
 * One request creates the client, the onboarding project and the handover, in
 * one transaction, and carries an idempotency key so a retry after a timeout
 * cannot create a second client. What sales promised is collected here so the
 * delivery owner never has to ask for it.
 */
export function ConvertDialog({
  deal,
  workspaceId,
  onClose,
}: {
  deal: Opportunity
  workspaceId: string
  onClose: () => void
}) {
  const mutation = useConvertOpportunity(workspaceId)
  const [scope, setScope] = useState(deal.scope_reference)
  const [commercial, setCommercial] = useState(deal.commercial_reference)
  const [exclusions, setExclusions] = useState('')
  const [owner, setOwner] = useState('')
  const [startOn, setStartOn] = useState('')
  const [endOn, setEndOn] = useState('')
  const [done, setDone] = useState<string | null>(null)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        opportunityId: deal.id,
        expectedVersion: deal.version,
        acceptedScopeEvidence: scope,
        commercialReference: commercial,
        deliveryOwnerId: owner,
        exclusions,
        ...(startOn ? { promisedStartOn: startOn } : {}),
        ...(endOn ? { promisedEndOn: endOn } : {}),
      },
      {
        onSuccess: (result) =>
          setDone(
            result.was_existing
              ? `${deal.service} was already converted. Nothing new was created.`
              : `${result.client.display_name} is now a client, and the onboarding project "${result.project.name}" is waiting for delivery to accept the handover.`,
          ),
      },
    )
  }

  if (done) {
    return (
      <Dialog title="Deal won" context={deal.service} onClose={onClose}>
        <p className={ui.success} role="status">
          {done}
        </p>
        <p className={ui.muted}>
          This records a contract, not money. Cash appears in reports only when
          a receipt is recorded against evidence.
        </p>
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.primary} onClick={onClose}>
            Done
          </button>
        </div>
      </Dialog>
    )
  }

  return (
    <Dialog
      title="Close as won"
      context={`${deal.service} · ${deal.contact_name}`}
      onClose={onClose}
    >
      <form onSubmit={submit} className={ui.form}>
        <p className={ui.notice}>
          This creates the client and the onboarding project together, and
          hands over to delivery. Delivery can accept the handover or return it
          naming what is missing.
        </p>
        <label className={ui.field}>
          <span>What scope did the client accept?</span>
          <span className={ui.hint}>
            The signed proposal, statement of work or email that records it.
          </span>
          <TextArea
            limit={LIMITS.longText}
            rows={3}
            required
            value={scope}
            onChange={setScope}
          />
        </label>
        <label className={ui.field}>
          <span>Commercial decision reference</span>
          <span className={ui.hint}>Contract number, order or approval reference.</span>
          <TextInput
            limit={LIMITS.reference}
            type="text"
            required
            value={commercial}
            onChange={setCommercial}
          />
        </label>
        <label className={ui.field}>
          <span>Exclusions (optional)</span>
          <span className={ui.hint}>What is explicitly not included.</span>
          <TextArea
            limit={LIMITS.longText}
            rows={2}
            value={exclusions}
            onChange={setExclusions}
          />
        </label>
        <div className={ui.formGrid}>
          <label className={ui.field}>
            <span>Delivery owner</span>
            <MemberSelect
              workspaceId={workspaceId}
              value={owner}
              onChange={setOwner}
              roles={DELIVERY_ROLES}
              required
            />
          </label>
          <label className={ui.field}>
            <span>Promised start (optional)</span>
            <input
              type="date"
              value={startOn}
              onChange={(event) => setStartOn(event.target.value)}
            />
          </label>
          <label className={ui.field}>
            <span>Promised end (optional)</span>
            <input
              type="date"
              min={startOn || undefined}
              value={endOn}
              onChange={(event) => setEndOn(event.target.value)}
            />
          </label>
        </div>
        <ErrorNotice error={mutation.error} fallback="The deal could not be closed as won." />
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Converting…' : 'Close as won and hand over'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
