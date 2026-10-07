import {
  ArrowRight,
  ChevronLeft,
  FolderKanban,
  LifeBuoy,
  Mail,
  MessageSquare,
  Phone,
  Users,
  Video,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useOutletContext, useParams } from 'react-router'

import styles from './ClientRecordPage.module.css'
import { Avatar, Person, Progress } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { formatDate, formatDateTime } from '@/lib/dates'
import {
  useActivities,
  useClient,
  useOpportunity,
  useProjects,
  useStageHistory,
  useTickets,
} from '@/lib/queries'
import type { SessionContext } from '@/lib/types'

const ACTIVITY_LABELS: Record<string, string> = {
  call: 'Call',
  email: 'Email',
  meeting: 'Meeting',
  linkedin: 'LinkedIn touch',
  note: 'Note',
}

const ACTIVITY_ICONS: Record<string, ReactNode> = {
  call: <Phone size={14} aria-hidden="true" />,
  email: <Mail size={14} aria-hidden="true" />,
  meeting: <Video size={14} aria-hidden="true" />,
  linkedin: <Users size={14} aria-hidden="true" />,
  note: <MessageSquare size={14} aria-hidden="true" />,
}

const STAGE_LABELS: Record<string, string> = {
  discovery: 'Discovery',
  qualified: 'Qualified',
  proposal: 'Proposal',
  negotiation: 'Negotiation',
  won: 'Won',
  lost: 'Lost',
}

/**
 * The customer record (CRM07, SVX-PRD-001 sections 1.3 and 7.2).
 *
 * One page with what was sold, what was promised, who delivers it, how the
 * sale went, what is being delivered and what has gone wrong. Delivery sees the
 * sales commitments here "without requesting another spreadsheet", and nobody
 * has to recreate the contact when a prospect becomes a client.
 */
export function ClientRecordPage() {
  const session = useOutletContext<SessionContext>()
  const { clientId = '' } = useParams()
  const ws = session.workspace.id
  const { data: client, isLoading, isError } = useClient(ws, clientId)

  const back = (
    <Link to="/clients" className={styles.back}>
      <ChevronLeft size={16} aria-hidden="true" />
      Clients
    </Link>
  )

  if (isLoading) {
    return (
      <div className={ui.page}>
        {back}
        <p role="status" aria-live="polite" className={ui.muted}>
          Loading the client…
        </p>
      </div>
    )
  }
  if (isError || !client) {
    return (
      <div className={ui.page}>
        {back}
        <p role="alert" className={ui.error}>
          This client could not be loaded.
        </p>
      </div>
    )
  }

  return (
    <div className={ui.page}>
      <header className={styles.hero}>
        {back}
        <div className={styles.heroRow}>
          <Avatar name={client.display_name} large />
          <div className={styles.heroText}>
            <h1>{client.display_name}</h1>
            <p className={ui.subtitle}>Client since the deal was closed as won.</p>
          </div>
          <span className={client.awaiting_handover ? ui.pillWarning : ui.pillSuccess}>
            {client.awaiting_handover ? 'Awaiting handover' : 'Handover accepted'}
          </span>
        </div>
      </header>

      <div className={ui.split}>
        <div className={ui.stack}>
          <SalesHistory
            workspaceId={ws}
            opportunityId={client.originating_opportunity}
            contactId={client.contact}
          />
          <Delivery workspaceId={ws} clientId={client.id} />
          <Support workspaceId={ws} clientId={client.id} />
        </div>

        <aside className={ui.stack}>
          <section aria-labelledby="people" className={`${ui.card} ${ui.cardFlush}`}>
            <div className={ui.panelHeader}>
              <h2 id="people">People</h2>
            </div>
            <div className={styles.people}>
              <div>
                <p className={styles.label}>Client contact</p>
                <Person name={client.contact_name} />
              </div>
              <div>
                <p className={styles.label}>Delivery owner</p>
                {client.delivery_owner_email ? (
                  <Person
                    name={client.delivery_owner_email.split('@')[0]!}
                    detail={client.delivery_owner_email}
                  />
                ) : (
                  <span className={styles.missing}>Not assigned</span>
                )}
              </div>
            </div>
          </section>

          <section aria-labelledby="commitments" className={`${ui.card} ${ui.cardFlush}`}>
            <div className={ui.panelHeader}>
              <h2 id="commitments">What was promised</h2>
            </div>
            <dl className={styles.promised}>
              <div>
                <dt>Accepted scope</dt>
                <dd>
                  {client.accepted_scope || <span className={styles.missing}>Not recorded</span>}
                </dd>
              </div>
              <div>
                <dt>Exclusions</dt>
                <dd>{client.exclusions || <span className={ui.muted}>None stated</span>}</dd>
              </div>
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
                <dd>
                  {client.promised_start_on || client.promised_end_on ? (
                    `${formatDate(client.promised_start_on)} to ${formatDate(client.promised_end_on)}`
                  ) : (
                    <span className={ui.muted}>Not set</span>
                  )}
                </dd>
              </div>
              {client.handover_returned_at ? (
                <div>
                  <dt>Returned to sales</dt>
                  <dd className={styles.returned}>{client.handover_returned_reason}</dd>
                </div>
              ) : null}
            </dl>
          </section>
        </aside>
      </div>
    </div>
  )
}

function SalesHistory({
  workspaceId,
  opportunityId,
  contactId,
}: {
  workspaceId: string
  opportunityId: string | null
  contactId: string
}) {
  const deal = useOpportunity(workspaceId, opportunityId)
  const history = useStageHistory(workspaceId, opportunityId)
  const activities = useActivities(workspaceId, { contact: contactId })
  const entries = history.data ?? []
  const touches = (activities.data?.results ?? []).slice(0, 20)

  return (
    <section aria-labelledby="sales-history" className={`${ui.card} ${ui.cardFlush}`}>
      <div className={ui.panelHeader}>
        <h2 id="sales-history">How the sale went</h2>
      </div>
      <div className={ui.panelBody}>
        {deal.data ? (
          <div className={styles.deal}>
            <div>
              <p className={styles.dealTitle}>{deal.data.service}</p>
              <p className={ui.muted} style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
                Sold by {deal.data.owner_email ?? 'unknown'}
              </p>
            </div>
            <p className={styles.dealValue}>
              {deal.data.amount === null
                ? 'Value not recorded'
                : new Intl.NumberFormat(undefined, {
                    style: 'currency',
                    currency: deal.data.currency || 'USD',
                  }).format(Number(deal.data.amount))}
            </p>
          </div>
        ) : opportunityId ? (
          <p className={ui.muted}>The originating deal is not visible to your role.</p>
        ) : (
          <p className={ui.muted}>This client was not created from a deal.</p>
        )}

        {entries.length > 0 ? (
          <>
            <h3 className={styles.subheading}>Stage history</h3>
            <ol className={styles.timeline}>
              {entries.map((entry) => (
                <li key={entry.id}>
                  <p className={styles.timelineTitle}>
                    {entry.from_stage ? STAGE_LABELS[entry.from_stage] ?? entry.from_stage : 'Created'}
                    <ArrowRight size={13} aria-label="to" />
                    {STAGE_LABELS[entry.to_stage] ?? entry.to_stage}
                  </p>
                  <p className={styles.timelineMeta}>
                    {formatDateTime(entry.changed_at)}
                    {entry.actor_email ? ` · ${entry.actor_email}` : ''}
                  </p>
                  {entry.reason ? <p className={styles.timelineNote}>{entry.reason}</p> : null}
                </li>
              ))}
            </ol>
          </>
        ) : null}

        <h3 className={styles.subheading}>Communication</h3>
        {touches.length === 0 ? (
          <p className={ui.muted}>Nothing recorded.</p>
        ) : (
          <ul className={styles.timeline}>
            {touches.map((activity) => (
              <li key={activity.id}>
                <p className={styles.timelineTitle}>
                  {ACTIVITY_ICONS[activity.kind]}
                  {ACTIVITY_LABELS[activity.kind] ?? activity.kind}
                  <span
                    className={
                      activity.evidence_type === 'provider_confirmed' ? ui.pillSuccess : ui.pill
                    }
                  >
                    {activity.evidence_type === 'provider_confirmed'
                      ? 'Provider-confirmed'
                      : 'Self-reported'}
                  </span>
                </p>
                <p className={styles.timelineMeta}>
                  {formatDateTime(activity.occurred_at)}
                  {activity.author_email ? ` · ${activity.author_email}` : ''}
                </p>
                {activity.outcome ? (
                  <p className={styles.timelineNote}>{activity.outcome}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function Delivery({ workspaceId, clientId }: { workspaceId: string; clientId: string }) {
  const { data } = useProjects(workspaceId, { client: clientId })
  const projects = data?.results ?? []
  return (
    <section aria-labelledby="delivery" className={`${ui.card} ${ui.cardFlush}`}>
      <div className={ui.panelHeader}>
        <h2 id="delivery">
          <FolderKanban size={16} aria-hidden="true" />
          Delivery
        </h2>
        <Link to="/projects" className={`${ui.ghost} ${ui.small}`}>
          Open projects
        </Link>
      </div>
      {projects.length === 0 ? (
        <p className={ui.panelEmpty}>No projects yet.</p>
      ) : (
        <ul className={ui.rows}>
          {projects.map((project) => {
            const counted = project.milestones.filter((m) => m.status !== 'cancelled')
            const accepted = counted.filter((m) => m.status === 'accepted').length
            const blocked = counted.filter((m) => m.status === 'blocked').length
            return (
              <li key={project.id} className={styles.projectRow}>
                <div className={styles.projectText}>
                  <strong>{project.name}</strong>
                  <span className={ui.meta}>
                    <span>
                      {accepted} of {counted.length} milestones accepted
                    </span>
                    {blocked ? <span className={ui.pillDanger}>{blocked} blocked</span> : null}
                  </span>
                </div>
                <div className={styles.projectProgress}>
                  <Progress
                    value={accepted}
                    total={counted.length}
                    label={`${project.name}: milestones accepted`}
                  />
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

const TICKET_LABEL: Record<string, string> = {
  new: 'New',
  triaged: 'Triaged',
  in_progress: 'In progress',
  waiting_customer: 'Waiting on customer',
  waiting_internal: 'Waiting internally',
  resolved: 'Resolved',
  closed: 'Closed',
}

const TICKET_STATE: Record<string, string | undefined> = {
  new: ui.pillInfo,
  triaged: ui.pillAccent,
  in_progress: ui.pillWarning,
  waiting_customer: ui.pill,
  resolved: ui.pillSuccess,
  closed: ui.pill,
}

function Support({ workspaceId, clientId }: { workspaceId: string; clientId: string }) {
  const { data } = useTickets(workspaceId, { client: clientId })
  const tickets = data?.results ?? []
  return (
    <section aria-labelledby="support" className={`${ui.card} ${ui.cardFlush}`}>
      <div className={ui.panelHeader}>
        <h2 id="support">
          <LifeBuoy size={16} aria-hidden="true" />
          Support
        </h2>
      </div>
      {tickets.length === 0 ? (
        <p className={ui.panelEmpty}>No tickets.</p>
      ) : (
        <ul className={ui.rows}>
          {tickets.map((ticket) => (
            <li key={ticket.id} className={styles.ticketRow}>
              <Link to={`/tickets?ticket=${ticket.id}`}>{ticket.title}</Link>
              <span className={ui.meta}>
                <span className={TICKET_STATE[ticket.state] ?? ui.pill}>
                  {TICKET_LABEL[ticket.state] ?? ticket.state}
                </span>
                <span className={ui.chip}>{ticket.priority}</span>
                {ticket.reopen_count ? (
                  <span className={ui.pillWarning}>Reopened {ticket.reopen_count}×</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
