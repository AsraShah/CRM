import { useState } from 'react'

import { TextArea, TextInput } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import ui from '@/components/ui.module.css'
import { LIMITS, MONEY_MAX, validators } from '@/lib/limits'
import { formatDate, todayLocalDate } from '@/lib/dates'
import { useClients, useReceipts, useRecordReceipt } from '@/lib/queries'
import type { CashReceipt, SessionContext } from '@/lib/types'

/**
 * The manual receipt register (CRM11).
 *
 * Nothing records money implicitly: winning a deal does not. A receipt needs an
 * evidence reference, and a mistake is corrected with an adjusting entry that
 * names the original and says why. The original is never edited.
 */
export function ReceiptsPanel({ session }: { session: SessionContext }) {
  const { data, isLoading, isError } = useReceipts(session.workspace.id)
  const [adjusting, setAdjusting] = useState<CashReceipt | null>(null)

  return (
    <section aria-labelledby="receipts-heading" className={ui.card}>
      <h2 id="receipts-heading" className={ui.sectionTitle}>
        Cash receipts
      </h2>
      <p className={ui.muted}>
        Record money when it arrives, against the bank or payment reference
        that proves it. This is the only source of the cash figure above.
      </p>

      <ReceiptForm
        key={adjusting?.id ?? 'new'}
        session={session}
        adjusting={adjusting}
        onDone={() => setAdjusting(null)}
      />

      {isLoading ? (
        <p role="status">Loading receipts…</p>
      ) : isError || !data ? (
        <p role="alert">Receipts could not be loaded.</p>
      ) : data.results.length === 0 ? (
        <p className={ui.muted}>No receipts recorded yet.</p>
      ) : (
        <div className={ui.tableWrap} style={{ marginTop: 'var(--space-4)' }}>
          <table className={ui.table}>
            <caption className="visually-hidden">
              Recorded cash receipts with client, amount, date and evidence.
            </caption>
            <thead>
              <tr>
                <th scope="col">Received</th>
                <th scope="col">Client</th>
                <th scope="col" className={ui.numeric}>
                  Amount
                </th>
                <th scope="col">Evidence</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((receipt) => (
                <tr key={receipt.id}>
                  <td>{formatDate(receipt.received_at)}</td>
                  <th scope="row">{receipt.client_name}</th>
                  <td className={ui.numeric}>
                    {format(receipt.currency, receipt.amount)}
                  </td>
                  <td>
                    {receipt.evidence_reference}
                    {receipt.adjusts ? (
                      <div className={ui.muted}>
                        Adjustment: {receipt.adjustment_reason}
                      </div>
                    ) : null}
                  </td>
                  <td>
                    <button
                      type="button"
                      className={ui.secondary}
                      aria-label={`Correct receipt ${receipt.evidence_reference}`}
                      onClick={() => setAdjusting(receipt)}
                    >
                      Correct
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function ReceiptForm({
  session,
  adjusting,
  onDone,
}: {
  session: SessionContext
  adjusting: CashReceipt | null
  onDone: () => void
}) {
  const mutation = useRecordReceipt(session.workspace.id)
  const { data: clients } = useClients(session.workspace.id)
  const [client, setClient] = useState(adjusting?.client ?? '')
  const [amount, setAmount] = useState('')
  const [currency, setCurrency] = useState(
    adjusting?.currency ?? session.workspace.default_currency,
  )
  const [receivedAt, setReceivedAt] = useState(todayLocalDate())
  const [evidence, setEvidence] = useState('')
  const [note, setNote] = useState('')
  const [reason, setReason] = useState('')
  const [saved, setSaved] = useState(false)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    setSaved(false)
    mutation.mutate(
      {
        client,
        amount,
        currency: currency.toUpperCase(),
        receivedAt,
        evidenceReference: evidence,
        note,
        ...(adjusting ? { adjusts: adjusting.id, adjustmentReason: reason } : {}),
      },
      {
        onSuccess: () => {
          setAmount('')
          setEvidence('')
          setNote('')
          setReason('')
          setSaved(true)
          if (adjusting) onDone()
        },
      },
    )
  }

  return (
    <form onSubmit={submit} className={ui.form}>
      {adjusting ? (
        <p className={ui.notice}>
          Correcting the receipt of {format(adjusting.currency, adjusting.amount)} from{' '}
          {adjusting.client_name} ({adjusting.evidence_reference}). Enter the
          adjusting amount — negative to reduce — and why. The original stays
          as it was.
        </p>
      ) : null}
      <div className={ui.formGrid}>
        <label className={ui.field}>
          <span>Client</span>
          <select
            required
            disabled={Boolean(adjusting)}
            value={client}
            onChange={(event) => setClient(event.target.value)}
          >
            <option value="">Choose a client</option>
            {(clients?.results ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className={ui.field}>
          <span>Amount</span>
          <input
            type="number"
            step="0.01"
            inputMode="decimal"
            required
            {...(adjusting ? {} : { min: '0.01' })}
            max={MONEY_MAX}
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </label>
        <label className={ui.field}>
          <span>Currency</span>
          <TextInput
            limit={LIMITS.currency}
            validate={validators.currency}
            type="text"
            required
            pattern="[A-Za-z]{3}"
            disabled={Boolean(adjusting)}
            value={currency}
            onChange={(value) => setCurrency(value.toUpperCase())}
          />
        </label>
        <label className={ui.field}>
          <span>Received on</span>
          <input
            type="date"
            required
            max={todayLocalDate()}
            value={receivedAt}
            onChange={(event) => setReceivedAt(event.target.value)}
          />
        </label>
        <label className={ui.field}>
          <span>Evidence reference</span>
          <span className={ui.hint}>Bank transaction or payment ID.</span>
          <TextInput
            limit={LIMITS.evidenceReference}
            type="text"
            required
            value={evidence}
            onChange={setEvidence}
          />
        </label>
      </div>
      {adjusting ? (
        <label className={ui.field}>
          <span>Why is it being corrected?</span>
          <TextArea
            limit={LIMITS.reason}
            rows={2}
            required
            value={reason}
            onChange={setReason}
          />
        </label>
      ) : (
        <label className={ui.field}>
          <span>Note (optional)</span>
          <TextInput limit={LIMITS.reason} type="text" value={note} onChange={setNote} />
        </label>
      )}
      <ErrorNotice error={mutation.error} fallback="The receipt could not be recorded." />
      {saved ? (
        <p className={ui.success} role="status">
          Receipt recorded.
        </p>
      ) : null}
      <div className={ui.actions}>
        <button type="submit" className={ui.primary} disabled={mutation.isPending}>
          {mutation.isPending
            ? 'Saving…'
            : adjusting
              ? 'Record correction'
              : 'Record receipt'}
        </button>
        {adjusting ? (
          <button type="button" className={ui.secondary} onClick={onDone}>
            Cancel correction
          </button>
        ) : null}
      </div>
    </form>
  )
}

function format(currency: string, amount: string): string {
  const value = Number(amount)
  if (Number.isNaN(value)) return `${currency} ${amount}`
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value)
  } catch {
    return `${currency} ${amount}`
  }
}
