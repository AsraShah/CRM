import { CalendarClock, Hourglass, KanbanSquare, Pencil } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext } from 'react-router'

import { ConvertDialog } from './ConvertDialog'
import { EditDealDialog } from './EditDealDialog'
import styles from './PipelinePage.module.css'
import { StageMenu } from './StageMenu'
import { EmptyState, PageHeader, Stat } from '@/components/kit'
import { SalesViewSwitch } from '@/components/SalesViewSwitch'
import ui from '@/components/ui.module.css'
import { useOpportunities } from '@/lib/queries'
import type { Opportunity, OpportunityStage, SessionContext } from '@/lib/types'

const STAGES: { key: OpportunityStage; label: string }[] = [
  { key: 'discovery', label: 'Discovery' },
  { key: 'qualified', label: 'Qualified' },
  { key: 'proposal', label: 'Proposal' },
  { key: 'negotiation', label: 'Negotiation' },
]

function money(amount: number, currency: string): string {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount)
}

/**
 * The pipeline (CRM03, SVX-PRD-001 section 7.2).
 *
 * Stage movement is driven by a menu, not drag-and-drop: "keyboard or menu
 * controls support stage movement without drag-and-drop" is the stated
 * acceptance example, and a menu is also the only version that works on a
 * phone and with a screen reader.
 *
 * Totals are shown per currency and never summed across them. A single
 * combined figure would require a conversion basis nobody has agreed (CRM11).
 */
export function PipelinePage() {
  const session = useOutletContext<SessionContext>()
  const [stageFilter, setStageFilter] = useState<string>('')
  // Held here so the dialog outlives the card: a won deal leaves the list.
  const [converting, setConverting] = useState<Opportunity | null>(null)
  const { data, isLoading, isError } = useOpportunities(
    session.workspace.id,
    stageFilter ? { stage: stageFilter } : undefined,
  )

  const header = (
    <PageHeader
      eyebrow="Sales"
      title="Leads and Deals"
      subtitle="Open deals by stage. Move a deal with its stage menu."
    >
      <SalesViewSwitch />
    </PageHeader>
  )

  if (isLoading) {
    return (
      <div className={ui.page}>
        {header}
        <p role="status" aria-live="polite" className={ui.muted}>
          Loading the pipeline…
        </p>
      </div>
    )
  }
  if (isError || !data) {
    return (
      <div className={ui.page}>
        {header}
        <p role="alert" className={ui.error}>
          The pipeline could not be loaded.
        </p>
      </div>
    )
  }

  const open = data.results.filter((deal) => STAGES.some((stage) => stage.key === deal.stage))
  const visibleStages = stageFilter ? STAGES.filter((s) => s.key === stageFilter) : STAGES

  return (
    <div className={ui.page}>
      {header}

      <CurrencyTotals deals={open} />

      <div className={ui.filterBar}>
        <label>
          <span className="visually-hidden">Stage</span>
          <select value={stageFilter} onChange={(event) => setStageFilter(event.target.value)}>
            <option value="">All open stages</option>
            {STAGES.map((stage) => (
              <option key={stage.key} value={stage.key}>
                {stage.label}
              </option>
            ))}
          </select>
        </label>
        <span className={ui.filterCount}>
          {open.length} open {open.length === 1 ? 'deal' : 'deals'}
        </span>
      </div>

      {open.length === 0 && !stageFilter ? (
        <div className={ui.card}>
          <EmptyState icon={<KanbanSquare size={22} aria-hidden="true" />} title="No open deals">
            Open a deal from a qualified lead and it appears here at Discovery.
          </EmptyState>
        </div>
      ) : (
        <div className={styles.board}>
          {visibleStages.map((stage) => {
            const deals = open.filter((deal) => deal.stage === stage.key)
            return (
              <section
                key={stage.key}
                className={styles.column}
                aria-labelledby={`stage-${stage.key}`}
              >
                <h2 id={`stage-${stage.key}`} className={styles.columnHeading}>
                  <span className={`${styles.stageDot} ${styles[stage.key]}`} aria-hidden="true" />
                  {stage.label}
                  <span className={styles.columnCount}> ({deals.length})</span>
                </h2>
                {deals.length === 0 ? (
                  <p className={styles.empty}>No deals at this stage.</p>
                ) : (
                  <ul className={styles.cards}>
                    {deals.map((deal) => (
                      <DealCard
                        key={deal.id}
                        deal={deal}
                        workspaceId={session.workspace.id}
                        canTransition={session.permissions.includes('opportunity.transition')}
                        canConvert={session.permissions.includes('opportunity.convert')}
                        onConvert={setConverting}
                      />
                    ))}
                  </ul>
                )}
              </section>
            )
          })}
        </div>
      )}

      {converting ? (
        <ConvertDialog
          deal={converting}
          workspaceId={session.workspace.id}
          onClose={() => setConverting(null)}
        />
      ) : null}
    </div>
  )
}

/**
 * Per-currency totals.
 *
 * Deals with no agreed value are counted separately rather than treated as
 * zero: "unknown" and "nothing" are different facts (section 4.1).
 */
function CurrencyTotals({ deals }: { deals: Opportunity[] }) {
  const totals = new Map<string, { sum: number; count: number }>()
  let unknownValue = 0

  for (const deal of deals) {
    if (deal.amount === null || deal.currency === '') {
      unknownValue += 1
      continue
    }
    const entry = totals.get(deal.currency) ?? { sum: 0, count: 0 }
    totals.set(deal.currency, { sum: entry.sum + Number(deal.amount), count: entry.count + 1 })
  }

  return (
    <section aria-labelledby="totals-heading" className={ui.stats}>
      <h2 id="totals-heading" className="visually-hidden">
        Open pipeline value
      </h2>
      {[...totals.entries()].map(([currency, { sum, count }], index) => (
        <Stat
          key={currency}
          feature={index === 0}
          label={`Open pipeline (${currency})`}
          value={money(sum, currency)}
          meta={`${count} valued ${count === 1 ? 'deal' : 'deals'}`}
        />
      ))}
      <Stat
        label="Value not yet known"
        value={`${unknownValue} ${unknownValue === 1 ? 'deal' : 'deals'}`}
        meta="Counted separately, never as zero"
      />
      <Stat label="Open deals" value={deals.length} meta="Across all open stages" />
    </section>
  )
}

interface DealCardProps {
  deal: Opportunity
  workspaceId: string
  canTransition: boolean
  canConvert: boolean
  onConvert: (deal: Opportunity) => void
}

function DealCard({ deal, workspaceId, canTransition, canConvert, onConvert }: DealCardProps) {
  const [editing, setEditing] = useState(false)
  const { workspace } = useOutletContext<SessionContext>()
  const stale = deal.days_in_stage !== null && deal.days_in_stage > 14
  return (
    <li className={styles.card}>
      <div className={styles.cardTop}>
        <div className={styles.cardHeading}>
          <p className={styles.cardTitle}>{deal.service}</p>
          <p className={styles.cardContact}>{deal.contact_name}</p>
        </div>
        {canTransition ? (
          <button
            type="button"
            className={ui.iconButton}
            aria-label={`Edit ${deal.service}`}
            title="Edit deal"
            onClick={() => setEditing(true)}
          >
            <Pencil size={15} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      <p className={styles.value}>
        <span className="visually-hidden">Value: </span>
        {deal.amount === null ? (
          // Never rendered as 0: unknown is shown as unknown.
          <span className={styles.unknown}>Not yet known</span>
        ) : (
          money(Number(deal.amount), deal.currency || 'USD')
        )}
      </p>

      <div className={ui.meta}>
        <span className={stale ? styles.stale : undefined}>
          <Hourglass size={13} aria-hidden="true" />
          {deal.days_in_stage === null
            ? 'Time in stage unknown'
            : `${deal.days_in_stage} ${deal.days_in_stage === 1 ? 'day' : 'days'} in stage`}
        </span>
        {deal.next_action_at ? (
          <span>
            <CalendarClock size={13} aria-hidden="true" />
            Next{' '}
            <time dateTime={deal.next_action_at}>
              {new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(
                new Date(deal.next_action_at),
              )}
            </time>
          </span>
        ) : (
          // Rule A02 exists precisely because this state causes drift.
          <span className={ui.pillWarning}>No next action</span>
        )}
      </div>

      {canTransition ? (
        <StageMenu
          deal={deal}
          workspaceId={workspaceId}
          canConvert={canConvert}
          onConvert={onConvert}
        />
      ) : null}

      {editing ? (
        <EditDealDialog
          deal={deal}
          workspaceId={workspaceId}
          defaultCurrency={workspace.default_currency}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </li>
  )
}
