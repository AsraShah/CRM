import ui from '@/components/ui.module.css'
import { useOperationalReport } from '@/lib/queries'
import type { CoverageRatio } from '@/lib/types'

const STAGE_LABELS: Record<string, string> = {
  discovery: 'Discovery',
  qualified: 'Qualified',
  proposal: 'Proposal',
  negotiation: 'Negotiation',
}

const STAGE_ORDER = ['discovery', 'qualified', 'proposal', 'negotiation']

const SOURCE_LABELS: Record<string, string> = {
  referral: 'Referral',
  website: 'Website',
  linkedin: 'LinkedIn',
  outbound: 'Outbound prospecting',
  event: 'Event',
  import: 'Spreadsheet import',
  other: 'Other',
  unknown: 'Unknown',
}

/**
 * Operational reports (CRM11).
 *
 * Every ratio is shown with its raw counts beside it, so a small sample is
 * visibly small and nobody has to trust a percentage on its own. Where there is
 * nothing to measure, the figure says so rather than showing 0% or 100%.
 */
export function OperationalPanel({
  workspaceId,
  period,
}: {
  workspaceId: string
  period: Record<string, string>
}) {
  const { data, isLoading, isError } = useOperationalReport(workspaceId, period)

  if (isLoading) {
    return (
      <p role="status" aria-live="polite">
        Loading operational reports…
      </p>
    )
  }
  if (isError || !data) {
    return <p role="alert">Operational reports could not be loaded.</p>
  }

  const { pilot_measures: pilot, delivery_health: health } = data

  return (
    <div className={ui.page}>
      <section aria-labelledby="pilot-heading" className={ui.card}>
        <h2 id="pilot-heading" className={ui.sectionTitle}>
          Pilot measures
        </h2>
        <p className={ui.muted}>{pilot.note}</p>
        <div className={ui.tableWrap}>
          <table className={ui.table}>
            <caption className="visually-hidden">
              Pilot measures with counts, percentage and proposed threshold.
            </caption>
            <thead>
              <tr>
                <th scope="col">Measure</th>
                <th scope="col" className={ui.numeric}>
                  Counts
                </th>
                <th scope="col" className={ui.numeric}>
                  Coverage
                </th>
                <th scope="col">Proposed threshold</th>
              </tr>
            </thead>
            <tbody>
              <Ratio label="Leads with an owner" ratio={pilot.ownership_coverage} />
              <Ratio
                label="Open deals with a future next action"
                ratio={pilot.next_action_coverage}
              />
              <Ratio
                label="Won deals with a complete handover"
                ratio={pilot.handover_completeness}
              />
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="source-heading" className={ui.card}>
        <h2 id="source-heading" className={ui.sectionTitle}>
          Conversion by source
        </h2>
        <p className={ui.muted}>{data.conversion_by_source.caveat}</p>
        {data.conversion_by_source.rows.length === 0 ? (
          <p className={ui.muted}>No leads were created in this period.</p>
        ) : (
          <div className={ui.tableWrap}>
            <table className={ui.table}>
              <caption className="visually-hidden">
                Leads created in the period by source, and how many were
                qualified, disqualified and won.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col" className={ui.numeric}>
                    Leads
                  </th>
                  <th scope="col" className={ui.numeric}>
                    Qualified
                  </th>
                  <th scope="col" className={ui.numeric}>
                    Disqualified
                  </th>
                  <th scope="col" className={ui.numeric}>
                    Won / leads
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.conversion_by_source.rows.map((row) => (
                  <tr key={row.source}>
                    <th scope="row">{SOURCE_LABELS[row.source] ?? row.source}</th>
                    <td className={ui.numeric}>{row.leads}</td>
                    <td className={ui.numeric}>{row.qualified}</td>
                    <td className={ui.numeric}>{row.disqualified}</td>
                    <td className={ui.numeric}>{row.won_per_lead}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-labelledby="aging-heading" className={ui.card}>
        <h2 id="aging-heading" className={ui.sectionTitle}>
          How long open deals have sat in their stage
        </h2>
        <div className={ui.tableWrap} style={{ marginTop: 'var(--space-3)' }}>
          <table className={ui.table}>
            <caption className="visually-hidden">
              Open deals per stage, with how many have been there over 7 and
              over 30 days.
            </caption>
            <thead>
              <tr>
                <th scope="col">Stage</th>
                <th scope="col" className={ui.numeric}>
                  Open deals
                </th>
                <th scope="col" className={ui.numeric}>
                  Over 7 days
                </th>
                <th scope="col" className={ui.numeric}>
                  Over 30 days
                </th>
                <th scope="col" className={ui.numeric}>
                  Age unknown
                </th>
              </tr>
            </thead>
            <tbody>
              {STAGE_ORDER.filter((stage) => data.stage_aging.stages[stage]).map(
                (stage) => {
                  const row = data.stage_aging.stages[stage]!
                  return (
                    <tr key={stage}>
                      <th scope="row">{STAGE_LABELS[stage]}</th>
                      <td className={ui.numeric}>{row.total}</td>
                      <td className={ui.numeric}>{row.over_7_days}</td>
                      <td className={ui.numeric}>{row.over_30_days}</td>
                      <td className={ui.numeric}>{row.age_unknown}</td>
                    </tr>
                  )
                },
              )}
            </tbody>
          </table>
        </div>
      </section>

      <div className={ui.grid}>
      <section aria-labelledby="evidence-heading" className={ui.card}>
        <h2 id="evidence-heading" className={ui.sectionTitle}>
          Activity evidence
        </h2>
        <dl className={`${ui.facts} ${ui.factsNumeric}`}>
          <dt>Self-reported</dt>
          <dd>{data.activity_evidence.self_reported}</dd>
          <dt>Provider-confirmed</dt>
          <dd>{data.activity_evidence.provider_confirmed}</dd>
        </dl>
        <p className={ui.muted} style={{ margin: 0 }}>
          {data.activity_evidence.interpretation}
        </p>
      </section>

      <section aria-labelledby="health-heading" className={ui.card}>
        <h2 id="health-heading" className={ui.sectionTitle}>
          Delivery health
        </h2>
        <dl className={`${ui.facts} ${ui.factsNumeric}`}>
          <dt>Blocked milestones</dt>
          <dd>{health.blocked_milestones.length}</dd>
          <dt>Dependency overrides recorded</dt>
          <dd>{health.dependency_overrides}</dd>
          <dt>Tickets reopened at least once</dt>
          <dd>{health.reopened_tickets}</dd>
          <dt>Waiting tickets past their review time</dt>
          <dd>{health.tickets_waiting_past_review}</dd>
        </dl>
        {health.blocked_milestones.length > 0 ? (
          <ul className={ui.list}>
            {health.blocked_milestones.map((m) => (
              <li key={m.id} className={ui.warning}>
                <strong>
                  {m.project}: {m.name}
                </strong>
                <br />
                {m.reason || 'No reason recorded.'}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
      </div>
    </div>
  )
}

function Ratio({ label, ratio }: { label: string; ratio: CoverageRatio }) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td className={ui.numeric}>
        {ratio.numerator} of {ratio.denominator}
      </td>
      <td className={ui.numeric}>
        {ratio.percentage === null ? (
          <span className={ui.muted}>Nothing to measure</span>
        ) : (
          `${ratio.percentage}%`
        )}
      </td>
      <td>{ratio.proposed_threshold}</td>
    </tr>
  )
}
