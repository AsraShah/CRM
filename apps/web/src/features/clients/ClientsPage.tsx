import { Briefcase, CalendarRange, Search } from 'lucide-react'
import { useState } from 'react'
import { Link, useOutletContext } from 'react-router'

import styles from './ClientsPage.module.css'
import { TextArea } from '@/components/fields'
import { Avatar, EmptyState, PageHeader, Person, Stat } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/dates'
import { useClients, useHandoverDecision } from '@/lib/queries'
import type { Client, SessionContext } from '@/lib/types'

/**
 * Clients and the handover queue (CRM07).
 *
 * The delivery owner sees the sales commitments without requesting another
 * spreadsheet — that is the stated acceptance example for this screen
 * (SVX-PRD-001 section 7.2). So the accepted scope, exclusions and commercial
 * reference are shown on the card itself, not behind a link.
 *
 * Accepting and returning are both first-class actions. Returning requires
 * saying what is missing, because a bare rejection leaves sales guessing.
 */
export function ClientsPage() {
  const session = useOutletContext<SessionContext>()
  const [showPendingOnly, setShowPendingOnly] = useState(true)
  const [query, setQuery] = useState('')
  const { data, isLoading, isError } = useClients(session.workspace.id)

  const canDecide = session.permissions.includes('handover.accept')

  const header = (
    <PageHeader
      eyebrow="Delivery"
      title="Clients"
      subtitle="Every won deal becomes a client. Delivery accepts the handover before work begins."
    />
  )

  if (isLoading) {
    return (
      <div className={ui.page}>
        {header}
        <p role="status" aria-live="polite" className={ui.muted}>
          Loading clients…
        </p>
      </div>
    )
  }
  if (isError || !data) {
    return (
      <div className={ui.page}>
        {header}
        <p role="alert" className={ui.error}>
          Clients could not be loaded.
        </p>
      </div>
    )
  }

  const needle = query.trim().toLowerCase()
  const pendingCount = data.results.filter((c) => c.awaiting_handover).length
  const clients = data.results.filter(
    (c) =>
      (!showPendingOnly || c.awaiting_handover) &&
      (!needle ||
        c.display_name.toLowerCase().includes(needle) ||
        (c.delivery_owner_email ?? '').toLowerCase().includes(needle)),
  )

  return (
    <div className={ui.page}>
      {header}

      <div className={ui.stats}>
        <Stat feature label="Clients" value={data.results.length} meta="All won deals" />
        <Stat
          label="Awaiting handover"
          value={pendingCount}
          meta={pendingCount > 0 ? 'Waiting for delivery to accept' : 'Nothing waiting'}
        />
        <Stat
          label="Handover accepted"
          value={data.results.length - pendingCount}
          meta="Delivery has taken ownership"
        />
      </div>

      <div className={ui.filterBar}>
        <label className={ui.search}>
          <Search size={16} aria-hidden="true" />
          <span className="visually-hidden">Search clients</span>
          <input
            type="search"
            maxLength={100}
            placeholder="Search by client or delivery owner"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className={ui.toggle}>
          <input
            type="checkbox"
            checked={showPendingOnly}
            onChange={(event) => setShowPendingOnly(event.target.checked)}
          />
          <span>Awaiting handover only</span>
          {pendingCount > 0 ? <span className={ui.pillWarning}>{pendingCount}</span> : null}
        </label>
        <span className={ui.filterCount}>
          {clients.length} {clients.length === 1 ? 'client' : 'clients'}
        </span>
      </div>

      {clients.length === 0 ? (
        <div className={ui.card}>
          <EmptyState
            icon={<Briefcase size={22} aria-hidden="true" />}
            title={
              showPendingOnly && !needle
                ? 'No handovers are waiting'
                : data.results.length === 0
                  ? 'No clients yet'
                  : 'No clients match'
            }
          >
            {showPendingOnly && !needle
              ? 'Every won deal has been accepted by delivery. Untick the filter to see all clients.'
              : data.results.length === 0
                ? 'Clients are created when a deal is closed as won.'
                : 'Try a different search.'}
          </EmptyState>
        </div>
      ) : (
        <ul className={styles.grid}>
          {clients.map((client) => (
            <ClientCard
              key={client.id}
              client={client}
              workspaceId={session.workspace.id}
              canDecide={canDecide}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

interface ClientCardProps {
  client: Client
  workspaceId: string
  canDecide: boolean
}

function ClientCard({ client, workspaceId, canDecide }: ClientCardProps) {
  const [mode, setMode] = useState<'none' | 'accept' | 'return'>('none')
  const [note, setNote] = useState('')
  const [missing, setMissing] = useState('')
  const mutation = useHandoverDecision(workspaceId)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        clientId: client.id,
        expectedVersion: client.version,
        accept: mode === 'accept',
        note,
        missingInformation: missing,
      },
      { onSuccess: () => setMode('none') },
    )
  }

  const promised =
    client.promised_start_on || client.promised_end_on
      ? `${client.promised_start_on ? formatDate(client.promised_start_on) : '—'} to ${
          client.promised_end_on ? formatDate(client.promised_end_on) : '—'
        }`
      : null

  return (
    <li className={client.awaiting_handover ? `${styles.card} ${styles.cardPending}` : styles.card}>
      <div className={styles.cardHeader}>
        <span className={styles.identity}>
          <Avatar name={client.display_name} large />
          <span className={styles.identityText}>
            <h2>
              <Link to={`/clients/${client.id}`}>{client.display_name}</Link>
            </h2>
            {/* The word carries the meaning; colour only reinforces it. */}
            <span className={client.awaiting_handover ? ui.pillWarning : ui.pillSuccess}>
              {client.awaiting_handover ? 'Awaiting handover' : 'Handover accepted'}
            </span>
          </span>
        </span>
      </div>

      {/* What sales promised, in full, on the card. */}
      <dl className={styles.commitments}>
        <div>
          <dt>Accepted scope</dt>
          <dd>{client.accepted_scope || <span className={styles.missing}>Not recorded</span>}</dd>
        </div>
        <div>
          <dt>Exclusions</dt>
          <dd>{client.exclusions || <span className={ui.muted}>None stated</span>}</dd>
        </div>
        <div className={styles.pair}>
          <div>
            <dt>Commercial reference</dt>
            <dd>
              {client.commercial_reference || (
                <span className={styles.missing}>Not recorded</span>
              )}
            </dd>
          </div>
          <div>
            <dt>Promised dates</dt>
            <dd className={styles.dates}>
              <CalendarRange size={14} aria-hidden="true" />
              {promised ?? <span className={ui.muted}>Not set</span>}
            </dd>
          </div>
        </div>
      </dl>

      <div className={styles.owner}>
        <span className={styles.ownerLabel}>Delivery owner</span>
        {client.delivery_owner_email ? (
          <Person
            name={client.delivery_owner_email.split('@')[0]!}
            detail={client.delivery_owner_email}
          />
        ) : (
          <span className={styles.missing}>Not assigned</span>
        )}
      </div>

      {client.handover_returned_at ? (
        <p className={ui.warning}>
          <strong>Returned to sales:</strong> {client.handover_returned_reason}
        </p>
      ) : null}

      {mutation.isError ? (
        <p role="alert" className={ui.error}>
          {mutation.error instanceof ApiError && mutation.error.isVersionConflict
            ? 'This handover changed while you were reading it. Reload before deciding.'
            : mutation.error instanceof ApiError
              ? mutation.error.message
              : 'The decision could not be saved.'}
        </p>
      ) : null}

      {canDecide && client.awaiting_handover ? (
        mode === 'none' ? (
          <div className={styles.footer}>
            {/* The accessible name begins with the visible text so voice
                control still works (WCAG 2.5.3), and names the client so the
                button is unambiguous in a list of handovers. */}
            <button
              type="button"
              className={ui.secondary}
              aria-label={`Return to sales: ${client.display_name}`}
              onClick={() => setMode('return')}
            >
              Return to sales
            </button>
            <button
              type="button"
              className={ui.primary}
              aria-label={`Accept handover for ${client.display_name}`}
              onClick={() => setMode('accept')}
            >
              Accept handover
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className={`${ui.form} ${styles.footerForm}`}>
            {mode === 'accept' ? (
              <label className={ui.field}>
                <span>Note (optional)</span>
                <TextArea
              limit={LIMITS.reason} rows={2} value={note} onChange={setNote} />
              </label>
            ) : (
              <label className={ui.field}>
                <span>What is missing? Be specific.</span>
                <TextArea
                  limit={LIMITS.reason}
                  rows={3}
                  required
                  value={missing}
                  onChange={setMissing}
                  placeholder="For example: no access credentials, no named client contact."
                />
              </label>
            )}
            <div className={ui.actionsEnd}>
              <button type="button" className={ui.secondary} onClick={() => setMode('none')}>
                Cancel
              </button>
              <button type="submit" className={ui.primary} disabled={mutation.isPending}>
                {mutation.isPending
                  ? 'Saving…'
                  : mode === 'accept'
                    ? 'Confirm acceptance'
                    : 'Return to sales'}
              </button>
            </div>
          </form>
        )
      ) : null}
    </li>
  )
}
