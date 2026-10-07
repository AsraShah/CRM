import { useState } from 'react'

import { TextInput } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import ui from '@/components/ui.module.css'
import { LIMITS, MONEY_MAX, validators } from '@/lib/limits'
import { useUpdateOpportunity } from '@/lib/queries'
import type { Opportunity } from '@/lib/types'

/**
 * Edit an open deal (CRM03).
 *
 * Stage is not here: it moves through the stage menu, which collects what each
 * stage requires. Clearing the value returns it to "not yet known", which the
 * pipeline shows as unknown rather than as zero.
 */
export function EditDealDialog({
  deal,
  workspaceId,
  defaultCurrency,
  onClose,
}: {
  deal: Opportunity
  workspaceId: string
  /** Offered when a value is first added to a deal that had none. */
  defaultCurrency: string
  onClose: () => void
}) {
  const mutation = useUpdateOpportunity(workspaceId)
  const [service, setService] = useState(deal.service)
  const [amount, setAmount] = useState(deal.amount ?? '')
  const [currency, setCurrency] = useState(deal.currency || defaultCurrency)
  const [closeOn, setCloseOn] = useState(deal.expected_close_on ?? '')

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const changes: Record<string, string | null> = {}
    if (service !== deal.service) changes.service = service
    if (amount !== (deal.amount ?? '')) changes.amount = amount === '' ? null : amount
    if (amount !== '' && currency.toUpperCase() !== deal.currency) {
      changes.currency = currency.toUpperCase()
    }
    if (closeOn !== (deal.expected_close_on ?? '')) changes.expected_close_on = closeOn || null
    if (Object.keys(changes).length === 0) {
      onClose()
      return
    }
    mutation.mutate(
      { opportunityId: deal.id, expectedVersion: deal.version, changes },
      { onSuccess: onClose },
    )
  }

  return (
    <Dialog title="Edit deal" context={`${deal.service} · ${deal.contact_name}`} onClose={onClose}>
      <form onSubmit={submit} className={ui.form}>
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
            <span>Value</span>
            <span className={ui.hint}>Leave blank if not yet agreed.</span>
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
              required={amount !== ''}
              value={currency}
              onChange={(value) => setCurrency(value.toUpperCase())}
            />
          </label>
          <label className={ui.field}>
            <span>Expected close</span>
            <input type="date" value={closeOn} onChange={(event) => setCloseOn(event.target.value)} />
          </label>
        </div>
        <ErrorNotice error={mutation.error} fallback="The deal could not be saved." />
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
