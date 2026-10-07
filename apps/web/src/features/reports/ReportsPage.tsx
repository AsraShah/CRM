import { Banknote, ChevronRight, Info, Target, TrendingUp } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useOutletContext } from 'react-router'

import { MeasureDialog } from './MeasureDialog'
import { OperationalPanel } from './OperationalPanel'
import styles from './ReportsPage.module.css'
import { ReceiptsPanel } from './ReceiptsPanel'
import { PageHeader } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { formatDate, todayLocalDate } from '@/lib/dates'
import { useOverview } from '@/lib/queries'
import type { MoneyByCurrency, SessionContext } from '@/lib/types'

const ATTENTION_LABELS: Record<string, string> = {
  leads_without_owner: 'Leads with no owner',
  open_deals_without_next_action: 'Open deals with no next action',
  overdue_tasks: 'Overdue follow-ups',
  handovers_awaiting_acceptance: 'Handovers awaiting acceptance',
  blocked_milestones: 'Blocked milestones',
  overdue_milestones: 'Overdue milestones',
  open_critical_tickets: 'Open critical tickets',
  reopened_tickets: 'Reopened tickets',
}

/**
 * The CEO overview (CRM11).
 *
 * The layout enforces the requirement rather than merely permitting it: open
 * pipeline, won contract value and cash received are three separate panels,
 * each with its own heading and its own currency breakdown. There is no total
 * row, because there is no agreed basis for combining currencies — and a
 * dashboard that quietly produces one would be the most misleading thing in
 * the product.
 *
 * Every figure states where it came from, and deals with no agreed value are
 * counted separately rather than folded in as zero.
 */
export function ReportsPage() {
  const session = useOutletContext<SessionContext>()
  const today = todayLocalDate()
  const [start, setStart] = useState(today.slice(0, 8) + '01')
  const [end, setEnd] = useState(today)
  const period = { start, end }
  const { data, isLoading, isError } = useOverview(session.workspace.id, period)
  const [drill, setDrill] = useState<{ measure: string; title: string } | null>(null)

  // The period picker stays on screen while the figures load, so changing it
  // never makes the control itself disappear.
  const header = (
    <PageHeader
      eyebrow="Insight"
      title="Overview"
      subtitle={
        data
          ? `${formatDate(data.period.start)} to ${formatDate(data.period.end)}${
              data.scope === 'own_records' ? ' · your records only' : ''
            }`
          : 'Pipeline, contracts, cash and delivery for a period.'
      }
      actions={
        <fieldset className={styles.period}>
          <legend className="visually-hidden">Period</legend>
          <label>
            <span>From</span>
            <input
              type="date"
              required
              max={end}
              value={start}
              onChange={(event) => event.target.value && setStart(event.target.value)}
            />
          </label>
          <label>
            <span>To</span>
            <input
              type="date"
              required
              min={start}
              value={end}
              onChange={(event) => event.target.value && setEnd(event.target.value)}
            />
          </label>
        </fieldset>
      }
    />
  )

  if (isLoading) {
    return (
      <div className={ui.page}>
        {header}
        <p role="status" aria-live="polite" className={ui.muted}>
          Loading the overview…
        </p>
      </div>
    )
  }
  if (isError || !data) {
    return (
      <div className={ui.page}>
        {header}
        <p role="alert" className={ui.error}>
          The overview could not be loaded.
        </p>
      </div>
    )
  }

  const attention = Object.entries(data.attention).filter(([, count]) => count > 0)

  return (
    <div className={ui.page}>
      {header}

      <div className={styles.money}>
        <MoneyPanel
          title="Open pipeline"
          caption="Deals we might still win."
          icon={<Target size={18} aria-hidden="true" />}
          money={data.open_pipeline_value}
          onShow={() => setDrill({ measure: 'open_pipeline_value', title: 'Open pipeline' })}
        />
        <MoneyPanel
          title="Won in this period"
          caption="Contracts agreed. Not money received."
          icon={<TrendingUp size={18} aria-hidden="true" />}
          tone="info"
          money={data.won_contract_value}
          onShow={() => setDrill({ measure: 'won_contract_value', title: 'Won in this period' })}
        />
        <section aria-labelledby="cash-heading" className={`${styles.panel} ${styles.panelCash}`}>
          <div className={styles.panelTop}>
            <span className={styles.panelIcon}>
              <Banknote size={18} aria-hidden="true" />
            </span>
            <div>
              <h2 id="cash-heading" className={styles.panelTitle}>
                Cash received
              </h2>
              <p className={styles.panelCaption}>Recorded by hand against evidence references.</p>
            </div>
          </div>
          {Object.keys(data.cash_received.amounts).length === 0 ? (
            <p className={styles.noValue}>Nothing recorded in this period.</p>
          ) : (
            <ul className={styles.amounts}>
              {Object.entries(data.cash_received.amounts).map(([currency, amount]) => (
                <li key={currency}>
                  <span className={styles.amount}>{format(currency, amount)}</span>
                </li>
              ))}
            </ul>
          )}
          <p className={styles.footnote}>{data.cash_received.note}</p>
          <button
            type="button"
            className={`${ui.secondary} ${ui.small} ${styles.panelButton}`}
            onClick={() => setDrill({ measure: 'cash_received', title: 'Cash received' })}
          >
            Show the receipts
          </button>
        </section>
      </div>

      <p className={styles.basisNote}>
        <Info size={16} aria-hidden="true" />
        <span>
          Figures are shown per currency and are never added together: doing so would need a
          conversion basis that has not been agreed. A won deal does not appear as cash until a
          receipt is recorded against evidence.
        </span>
      </p>

      <section aria-labelledby="attention-heading" className={`${ui.card} ${ui.cardFlush}`}>
        <div className={ui.panelHeader}>
          <h2 id="attention-heading">
            Needs attention
            <span className={ui.count}> ({attention.length})</span>
          </h2>
        </div>
        <div className={ui.panelBody}>
          {attention.length === 0 ? (
            <p className={styles.noValue}>Nothing is currently flagged.</p>
          ) : (
            <ul className={styles.attentionList}>
              {attention.map(([key, count]) => (
                <li key={key}>
                  {/* Each figure opens the records behind it (CRM11). */}
                  <button
                    type="button"
                    className={styles.attentionItem}
                    onClick={() =>
                      setDrill({
                        measure: key,
                        title: ATTENTION_LABELS[key] ?? key.replace(/_/g, ' '),
                      })
                    }
                  >
                    <span className={styles.attentionCount}>{count}</span>
                    <span className={styles.attentionLabel}>
                      {ATTENTION_LABELS[key] ?? key.replace(/_/g, ' ')}
                    </span>
                    <ChevronRight size={16} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className={styles.footnote} style={{ marginTop: 'var(--space-3)' }}>
            Each figure counts specific records in a specific state. These are prompts to look at
            something, not judgements about anybody.
          </p>
        </div>
      </section>

      {session.permissions.includes('receipt.record') ? <ReceiptsPanel session={session} /> : null}

      <OperationalPanel workspaceId={session.workspace.id} period={period} />

      {drill ? (
        <MeasureDialog
          workspaceId={session.workspace.id}
          measure={drill.measure}
          title={drill.title}
          period={period}
          onClose={() => setDrill(null)}
        />
      ) : null}
    </div>
  )
}

function MoneyPanel({
  title,
  caption,
  icon,
  money,
  tone,
  onShow,
}: {
  title: string
  caption: string
  icon: ReactNode
  money: MoneyByCurrency
  tone?: 'info'
  onShow: () => void
}) {
  const id = title.toLowerCase().replace(/\s+/g, '-')
  const currencies = Object.entries(money.amounts)

  return (
    <section
      aria-labelledby={`${id}-heading`}
      className={tone === 'info' ? `${styles.panel} ${styles.panelInfo}` : styles.panel}
    >
      <div className={styles.panelTop}>
        <span className={styles.panelIcon}>{icon}</span>
        <div>
          <h2 id={`${id}-heading`} className={styles.panelTitle}>
            {title}
          </h2>
          <p className={styles.panelCaption}>{caption}</p>
        </div>
      </div>

      {currencies.length === 0 ? (
        <p className={styles.noValue}>No valued deals.</p>
      ) : (
        <ul className={styles.amounts}>
          {currencies.map(([currency, amount]) => (
            <li key={currency}>
              <span className={styles.amount}>{format(currency, amount)}</span>
            </li>
          ))}
        </ul>
      )}

      <p className={styles.meta}>
        {money.record_count} {money.record_count === 1 ? 'deal' : 'deals'}
        {money.unknown_count > 0 ? (
          <>
            {' · '}
            {/* Never rendered as zero: "unknown" and "nothing" are different. */}
            <span className={styles.unknown}>{money.unknown_count} with no agreed value</span>
          </>
        ) : null}
      </p>
      <button
        type="button"
        className={`${ui.secondary} ${ui.small} ${styles.panelButton}`}
        onClick={onShow}
      >
        Show the deals
      </button>
    </section>
  )
}

function format(currency: string, amount: string): string {
  const value = Number(amount)
  if (Number.isNaN(value)) return `${currency} ${amount}`
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value)
  } catch {
    // An unrecognised currency code is still shown, just unformatted.
    return `${currency} ${amount}`
  }
}
