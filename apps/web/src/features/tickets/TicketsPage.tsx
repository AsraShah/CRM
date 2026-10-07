import { Clock, Inbox, LifeBuoy, Lock, Plus, Search, Users } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router'

import { NewTicketDialog } from './NewTicketDialog'
import { NextActionField, TicketActions } from './TicketActions'
import styles from './TicketsPage.module.css'
import { TextArea } from '@/components/fields'
import { Avatar, EmptyState, PageHeader, Stat } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { ApiError } from '@/lib/api'
import { formatDate, formatDateTime } from '@/lib/dates'
import { useAddTicketComment, useTicketComments, useTickets } from '@/lib/queries'
import type { SessionContext, Ticket, TicketPriority, TicketState } from '@/lib/types'

const STATE_LABELS: Record<TicketState, string> = {
  new: 'New',
  triaged: 'Triaged',
  in_progress: 'In progress',
  // Kept distinct in the interface, because they are different problems.
  waiting_customer: 'Waiting on the customer',
  waiting_internal: 'Waiting on us',
  resolved: 'Resolved',
  closed: 'Closed',
}

const STATE_TONE: Record<TicketState, string | undefined> = {
  new: ui.pillInfo,
  triaged: ui.pillAccent,
  in_progress: ui.pillInfo,
  waiting_customer: ui.pill,
  waiting_internal: ui.pillWarning,
  resolved: ui.pillSuccess,
  closed: ui.pill,
}

const PRIORITY_LABELS: Record<TicketPriority, string> = {
  low: 'Low',
  normal: 'Normal',
  high: 'High',
  critical: 'Critical',
}

/**
 * Tickets (CRM09).
 *
 * The one visual rule that matters here: an internal note and a client-visible
 * comment must never be mistaken for each other. Internal notes carry a lock,
 * a label and a distinct background — three signals, because getting this wrong
 * is the kind of mistake that ends a client relationship.
 */
export function TicketsPage() {
  const session = useOutletContext<SessionContext>()
  const [openOnly, setOpenOnly] = useState(true)
  const [query, setQuery] = useState('')
  // The id, not the object: a stored copy goes stale after any change, and its
  // old version would turn the next action into a version conflict.
  // ?ticket= selects one directly, which is how an alert links to its record.
  const [params] = useSearchParams()
  const [selectedId, setSelectedId] = useState<string | null>(params.get('ticket'))
  const [creating, setCreating] = useState(false)
  const { data, isLoading, isError } = useTickets(session.workspace.id)

  const header = (
    <PageHeader
      eyebrow="Delivery"
      title="Tickets"
      subtitle="Client requests and internal issues, from report to tested resolution."
      actions={
        session.permissions.includes('ticket.manage') ? (
          <button type="button" className={ui.primary} onClick={() => setCreating(true)}>
            <Plus size={16} aria-hidden="true" />
            New ticket
          </button>
        ) : null
      }
    />
  )

  if (isLoading) {
    return (
      <div className={ui.page}>
        {header}
        <p role="status" aria-live="polite" className={ui.muted}>
          Loading tickets…
        </p>
      </div>
    )
  }
  if (isError || !data) {
    return (
      <div className={ui.page}>
        {header}
        <p role="alert" className={ui.error}>
          Tickets could not be loaded.
        </p>
      </div>
    )
  }

  const needle = query.trim().toLowerCase()
  const tickets = data.results.filter(
    (t) =>
      (!openOnly || t.is_open) &&
      (!needle ||
        t.title.toLowerCase().includes(needle) ||
        (t.client_name ?? '').toLowerCase().includes(needle)),
  )
  const selected = data.results.find((t) => t.id === selectedId) ?? null
  const open = data.results.filter((t) => t.is_open)

  return (
    <div className={ui.page}>
      {header}

      <div className={ui.stats}>
        <Stat feature label="Open" value={open.length} meta="Not yet resolved" />
        <Stat
          label="Critical"
          value={open.filter((t) => t.priority === 'critical').length}
          meta="Open and critical"
        />
        <Stat
          label="Review overdue"
          value={open.filter((t) => t.waiting_overdue).length}
          meta="Waiting past the review date"
        />
        <Stat
          label="Reopened"
          value={data.results.filter((t) => t.reopen_count > 0).length}
          meta="Came back after resolution"
        />
      </div>

      <div className={ui.filterBar}>
        <label className={ui.search}>
          <Search size={16} aria-hidden="true" />
          <span className="visually-hidden">Search tickets</span>
          <input
            type="search"
            maxLength={100}
            placeholder="Search by title or client"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className={ui.toggle}>
          <input
            type="checkbox"
            checked={openOnly}
            onChange={(event) => setOpenOnly(event.target.checked)}
          />
          <span>Open only</span>
        </label>
        <span className={ui.filterCount}>
          {tickets.length} {tickets.length === 1 ? 'ticket' : 'tickets'}
        </span>
      </div>

      <div className={styles.layout}>
        <section
          aria-labelledby="ticket-list-heading"
          className={`${ui.card} ${ui.cardFlush} ${styles.listPane}`}
        >
          <h2 id="ticket-list-heading" className="visually-hidden">
            Ticket list
          </h2>
          {tickets.length === 0 ? (
            <EmptyState icon={<Inbox size={22} aria-hidden="true" />} title="No tickets">
              {data.results.length === 0
                ? 'Nothing has been reported yet.'
                : 'No tickets match this filter.'}
            </EmptyState>
          ) : (
            <ul className={styles.list}>
              {tickets.map((ticket) => {
                const active = selected?.id === ticket.id
                return (
                  <li key={ticket.id}>
                    <button
                      type="button"
                      data-ticket-row
                      className={active ? `${styles.ticket} ${styles.ticketActive}` : styles.ticket}
                      onClick={() => setSelectedId(ticket.id)}
                      aria-current={active}
                    >
                      <span className={styles.ticketTop}>
                        <span
                          className={`${styles.priority} ${styles[`priority_${ticket.priority}`]}`}
                          aria-hidden="true"
                        />
                        <span className={styles.ticketTitle}>{ticket.title}</span>
                        <time className={styles.ticketDate} dateTime={ticket.created_at}>
                          {formatDate(ticket.created_at)}
                        </time>
                      </span>
                      {ticket.client_name ? (
                        <span className={styles.ticketClient}>{ticket.client_name}</span>
                      ) : null}
                      <span className={styles.ticketMeta}>
                        {ticket.priority === 'critical' ? (
                          <span className={ui.pillDanger}>Critical</span>
                        ) : null}
                        <span className={STATE_TONE[ticket.state]}>
                          {STATE_LABELS[ticket.state]}
                        </span>
                        {ticket.reopen_count > 0 ? (
                          <span className={ui.pillWarning}>Reopened {ticket.reopen_count}×</span>
                        ) : null}
                        {ticket.waiting_overdue ? (
                          <span className={ui.pillDanger}>Review overdue</span>
                        ) : null}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <section
          aria-labelledby="ticket-detail-heading"
          className={`${ui.card} ${ui.cardFlush} ${styles.detailPane}`}
        >
          <h2 id="ticket-detail-heading" className="visually-hidden">
            Ticket detail
          </h2>
          {selected ? (
            <TicketDetail key={selected.id} ticket={selected} session={session} />
          ) : (
            <EmptyState
              icon={<LifeBuoy size={22} aria-hidden="true" />}
              title="Select a ticket"
            >
              Select a ticket to see its correspondence.
            </EmptyState>
          )}
        </section>
      </div>

      {creating ? (
        <NewTicketDialog
          session={session}
          onClose={() => setCreating(false)}
          onCreated={(ticket) => {
            setCreating(false)
            setSelectedId(ticket.id)
          }}
        />
      ) : null}
    </div>
  )
}

function TicketDetail({ ticket, session }: { ticket: Ticket; session: SessionContext }) {
  const { data: comments, isLoading } = useTicketComments(session.workspace.id, ticket.id)
  const [body, setBody] = useState('')
  const [visibility, setVisibility] = useState<'internal' | 'client_visible'>('internal')
  const addComment = useAddTicketComment(session.workspace.id, ticket.id)

  const canPublish =
    session.role === 'owner' ||
    session.role === 'delivery_manager' ||
    session.role === 'sales_manager'

  function submit(event: React.FormEvent) {
    event.preventDefault()
    addComment.mutate(
      { body, visibility },
      {
        onSuccess: () => {
          setBody('')
          // Reset to internal after every post. A sticky "client visible"
          // setting is how an internal note eventually goes out by accident.
          setVisibility('internal')
        },
      },
    )
  }

  return (
    <article className={styles.detail}>
      <header className={styles.detailHeader}>
        <div className={styles.detailHeading}>
          <h3 className={styles.detailTitle}>{ticket.title}</h3>
          <p className={ui.meta}>
            <span className={STATE_TONE[ticket.state]}>{STATE_LABELS[ticket.state]}</span>
            <span
              className={
                ticket.priority === 'critical'
                  ? ui.pillDanger
                  : ticket.priority === 'high'
                    ? ui.pillWarning
                    : ui.pill
              }
            >
              {PRIORITY_LABELS[ticket.priority]} priority
            </span>
            {ticket.client_name ? <span>{ticket.client_name}</span> : null}
            {ticket.owner_email ? <span>Owner: {ticket.owner_email}</span> : null}
          </p>
        </div>
      </header>

      <div className={styles.detailBody}>
        {ticket.description ? <p className={styles.description}>{ticket.description}</p> : null}

        {ticket.state === 'waiting_customer' || ticket.state === 'waiting_internal' ? (
          <p className={ui.warning}>
            <Clock size={13} aria-hidden="true" /> <strong>Waiting:</strong>{' '}
            {ticket.waiting_reason}
            {ticket.waiting_review_at ? (
              <>
                {' '}
                · review by{' '}
                <time dateTime={ticket.waiting_review_at}>
                  {formatDate(ticket.waiting_review_at)}
                </time>
              </>
            ) : null}
          </p>
        ) : null}

        {ticket.resolution_note ? (
          <div className={ui.success}>
            <p style={{ margin: 0 }}>
              <strong>Resolution:</strong> {ticket.resolution_note}
            </p>
            <p style={{ margin: 'var(--space-1) 0 0' }}>
              <strong>Closure test:</strong> {ticket.closure_test_result}
            </p>
          </div>
        ) : null}

        <NextActionField ticket={ticket} session={session} />
        <TicketActions ticket={ticket} session={session} />
      </div>

      <div className={styles.thread}>
        <h4 className={styles.threadHeading}>
          Correspondence
          <span className={ui.count}> ({(comments ?? []).length})</span>
        </h4>
        {isLoading ? (
          <p role="status" className={ui.muted}>
            Loading…
          </p>
        ) : (comments ?? []).length === 0 ? (
          <p className={ui.muted} style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
            No comments yet.
          </p>
        ) : (
          <ul className={styles.comments}>
            {(comments ?? []).map((comment) => (
              <li
                key={comment.id}
                className={
                  comment.visibility === 'internal' ? styles.commentInternal : styles.commentClient
                }
              >
                <Avatar name={comment.author_email ?? 'Unknown'} />
                <div className={styles.commentBubble}>
                  <p className={styles.commentHeader}>
                    <span className={styles.commentAuthor}>
                      {comment.author_email ?? 'Unknown'}
                    </span>
                    {comment.visibility === 'internal' ? (
                      <span className={styles.visibilityInternal}>
                        <Lock size={12} aria-hidden="true" />
                        Internal only
                      </span>
                    ) : (
                      <span className={styles.visibilityClient}>
                        <Users size={12} aria-hidden="true" />
                        Visible to the client
                      </span>
                    )}
                    <time className={styles.commentTime} dateTime={comment.created_at}>
                      {formatDateTime(comment.created_at)}
                    </time>
                  </p>
                  <p className={styles.commentBody}>{comment.body}</p>
                </div>
              </li>
            ))}
          </ul>
        )}

        {addComment.isError ? (
          <p role="alert" className={ui.error}>
            {addComment.error instanceof ApiError
              ? addComment.error.message
              : 'The comment could not be added.'}
          </p>
        ) : null}

        <form
          onSubmit={submit}
          className={
            visibility === 'client_visible'
              ? `${styles.composer} ${styles.composerPublic}`
              : styles.composer
          }
        >
          <label className={ui.field}>
            <span className="visually-hidden">Add a comment</span>
            <TextArea
              limit={LIMITS.comment}
              rows={3}
              required
              placeholder={
                visibility === 'internal'
                  ? 'Write an internal note…'
                  : 'Write a reply the client will read…'
              }
              value={body}
              onChange={setBody}
            />
          </label>

          {visibility === 'client_visible' ? (
            <p className={ui.warning} role="status">
              This comment will be readable by the client once the portal is available. Internal
              context does not belong here.
            </p>
          ) : null}

          <div className={styles.composerBar}>
            <fieldset className={styles.visibilityChoice}>
              <legend className="visually-hidden">Who can read this?</legend>
              <label className={visibility === 'internal' ? styles.segActive : styles.seg}>
                <input
                  type="radio"
                  name="visibility"
                  className="visually-hidden"
                  checked={visibility === 'internal'}
                  onChange={() => setVisibility('internal')}
                />
                <Lock size={13} aria-hidden="true" /> Internal only
              </label>
              <label
                className={
                  !canPublish
                    ? styles.segDisabled
                    : visibility === 'client_visible'
                      ? styles.segActive
                      : styles.seg
                }
                title={canPublish ? undefined : 'Only a manager can publish to the client'}
              >
                <input
                  type="radio"
                  name="visibility"
                  className="visually-hidden"
                  disabled={!canPublish}
                  checked={visibility === 'client_visible'}
                  onChange={() => setVisibility('client_visible')}
                />
                <Users size={13} aria-hidden="true" /> Visible to the client
              </label>
            </fieldset>
            <button type="submit" className={ui.primary} disabled={addComment.isPending}>
              {addComment.isPending ? 'Saving…' : 'Add comment'}
            </button>
          </div>
        </form>
      </div>
    </article>
  )
}
