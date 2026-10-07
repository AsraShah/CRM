import { ChevronRight, Plus, Search, Upload, Users } from 'lucide-react'
import { useState } from 'react'
import { Link, useOutletContext, useSearchParams } from 'react-router'

import { LeadDialog } from './LeadDialog'
import styles from './LeadsPage.module.css'
import { NewLeadDialog, SOURCE_LABELS } from './NewLeadDialog'
import { Avatar, EmptyState, PageHeader, Person } from '@/components/kit'
import { SalesViewSwitch } from '@/components/SalesViewSwitch'
import ui from '@/components/ui.module.css'
import { formatDate } from '@/lib/dates'
import { useLeads } from '@/lib/queries'
import type { LeadStatus, SessionContext } from '@/lib/types'

const STATUS_LABELS: Record<LeadStatus, string> = {
  new: 'New',
  assigned: 'Assigned',
  contacting: 'Contacting',
  qualified: 'Qualified',
  nurture: 'Nurture',
  disqualified: 'Disqualified',
}

/** Badge tone per qualification status. The word always carries the meaning. */
export const STATUS_TONE: Record<LeadStatus, string | undefined> = {
  new: ui.pillInfo,
  assigned: ui.pillAccent,
  contacting: ui.pillWarning,
  qualified: ui.pillSuccess,
  nurture: ui.pill,
  disqualified: ui.pillDanger,
}

/**
 * Lead list (CRM03).
 *
 * Lead *status* is qualification; opportunity *stage* is one particular sale.
 * They are kept visibly separate, as the brief requires, so nobody reads
 * "Qualified" here as a deal that is progressing.
 *
 * Unowned leads are called out rather than shown as a blank cell: an
 * unassigned lead is the thing rule A01 exists to catch.
 */
export function LeadsPage() {
  const session = useOutletContext<SessionContext>()
  const [status, setStatus] = useState('')
  const [unownedOnly, setUnownedOnly] = useState(false)
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  // The id, not the row: after a change the row refetches with a new version.
  // ?lead= opens one directly, which is how an alert links to its record.
  const [params] = useSearchParams()
  const [openId, setOpenId] = useState<string | null>(params.get('lead'))

  const { data, isLoading, isError } = useLeads(
    session.workspace.id,
    status ? { status } : undefined,
  )

  const canCreate =
    session.permissions.includes('lead.manage') && session.permissions.includes('contact.manage')

  const header = (
    <PageHeader
      eyebrow="Sales"
      title="Leads and Deals"
      subtitle="Everyone you are talking to, and how far each conversation has got."
      actions={
        <>
          {session.permissions.includes('import.run') ? (
            <Link className={ui.secondary} to="/import">
              <Upload size={16} aria-hidden="true" />
              Import a spreadsheet
            </Link>
          ) : null}
          {canCreate ? (
            <button type="button" className={ui.primary} onClick={() => setCreating(true)}>
              <Plus size={16} aria-hidden="true" />
              New lead
            </button>
          ) : null}
        </>
      }
    >
      <SalesViewSwitch />
    </PageHeader>
  )

  if (isLoading) {
    return (
      <div className={ui.page}>
        {header}
        <p role="status" aria-live="polite" className={ui.muted}>
          Loading leads…
        </p>
      </div>
    )
  }
  if (isError || !data) {
    return (
      <div className={ui.page}>
        {header}
        <p role="alert" className={ui.error}>
          Leads could not be loaded.
        </p>
      </div>
    )
  }

  const needle = query.trim().toLowerCase()
  const rows = data.results.filter(
    (lead) =>
      (!unownedOnly || !lead.owner) &&
      (!needle ||
        lead.contact_name.toLowerCase().includes(needle) ||
        (lead.owner_email ?? '').toLowerCase().includes(needle)),
  )
  const open = data.results.find((lead) => lead.id === openId) ?? null
  const unassigned = data.results.filter((lead) => !lead.owner).length

  return (
    <div className={ui.page}>
      {header}

      <div className={ui.filterBar}>
        <label className={ui.search}>
          <Search size={16} aria-hidden="true" />
          <span className="visually-hidden">Search leads</span>
          <input
            type="search"
            maxLength={100}
            placeholder="Search by name or owner"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className={styles.filter}>
          <span className="visually-hidden">Status</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className={ui.toggle}>
          <input
            type="checkbox"
            checked={unownedOnly}
            onChange={(event) => setUnownedOnly(event.target.checked)}
          />
          <span>Unassigned only</span>
          {unassigned > 0 ? <span className={ui.pillWarning}>{unassigned}</span> : null}
        </label>
        <span className={ui.filterCount}>
          {rows.length} {rows.length === 1 ? 'lead' : 'leads'}
        </span>
      </div>

      {rows.length === 0 ? (
        <div className={ui.card}>
          <EmptyState
            icon={<Users size={22} aria-hidden="true" />}
            title={data.results.length === 0 ? 'No leads yet' : 'No leads match'}
            action={
              canCreate && data.results.length === 0 ? (
                <button type="button" className={ui.primary} onClick={() => setCreating(true)}>
                  <Plus size={16} aria-hidden="true" />
                  New lead
                </button>
              ) : null
            }
          >
            {data.results.length === 0
              ? 'Add your first lead, or import the spreadsheet you use today.'
              : 'Try a different search or filter.'}
          </EmptyState>
        </div>
      ) : (
        <div className={ui.tableWrap}>
          <table className={ui.table}>
            <caption className="visually-hidden">
              Leads, showing contact, owner, qualification status and source.
            </caption>
            <thead>
              <tr>
                <th scope="col">Contact</th>
                <th scope="col">Owner</th>
                <th scope="col">Status</th>
                <th scope="col">Source</th>
                <th scope="col">Added</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((lead) => (
                <tr key={lead.id} className={styles.row} onClick={() => setOpenId(lead.id)}>
                  <th scope="row">
                    <span className={styles.contact}>
                      <Avatar name={lead.contact_name} />
                      <span>{lead.contact_name}</span>
                    </span>
                  </th>
                  <td>
                    {lead.owner_email ? (
                      <Person name={lead.owner_email.split('@')[0]!} detail={lead.owner_email} />
                    ) : (
                      <span className={ui.pillWarning}>Unassigned</span>
                    )}
                  </td>
                  <td>
                    <span className={STATUS_TONE[lead.status]}>
                      {STATUS_LABELS[lead.status] ?? lead.status}
                    </span>
                  </td>
                  <td className={ui.muted}>{SOURCE_LABELS[lead.source] ?? lead.source}</td>
                  <td className={ui.muted}>
                    <time dateTime={lead.created_at}>{formatDate(lead.created_at)}</time>
                  </td>
                  <td className={ui.numeric}>
                    <button
                      type="button"
                      className={`${ui.ghost} ${ui.small}`}
                      aria-label={`Open ${lead.contact_name}`}
                      onClick={(event) => {
                        event.stopPropagation()
                        setOpenId(lead.id)
                      }}
                    >
                      Open
                      <ChevronRight size={14} aria-hidden="true" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {creating ? <NewLeadDialog session={session} onClose={() => setCreating(false)} /> : null}
      {open ? (
        <LeadDialog key={open.id} lead={open} session={session} onClose={() => setOpenId(null)} />
      ) : null}
    </div>
  )
}
