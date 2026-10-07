import { useState } from 'react'

import styles from './PipelinePage.module.css'
import { TextInput } from '@/components/fields'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { ApiError } from '@/lib/api'
import { useTransitionOpportunity } from '@/lib/queries'
import type { Opportunity, OpportunityStage } from '@/lib/types'

/**
 * Stage movement without drag-and-drop (SVX-PRD-001 section 7.2).
 *
 * Only transitions the server permits are offered, and the extra information a
 * target stage requires is collected before the request is sent — so a refusal
 * is the exception rather than the normal path.
 */
const ALLOWED_TRANSITIONS: Record<string, OpportunityStage[]> = {
  discovery: ['qualified', 'lost'],
  qualified: ['discovery', 'proposal', 'lost'],
  proposal: ['qualified', 'negotiation', 'lost'],
  negotiation: ['proposal', 'won', 'lost'],
}

const STAGE_LABELS: Record<string, string> = {
  discovery: 'Discovery',
  qualified: 'Qualified',
  proposal: 'Proposal',
  negotiation: 'Negotiation',
  won: 'Won',
  lost: 'Lost',
}

interface Props {
  deal: Opportunity
  workspaceId: string
  /** Closing as won is held above representative level (CRM07). */
  canConvert: boolean
  /**
   * Opens the conversion dialog. It lives on the page, not here: once the deal
   * is won it leaves the open pipeline, this card unmounts, and a dialog owned
   * by it would vanish before the user saw the result.
   */
  onConvert: (deal: Opportunity) => void
}

export function StageMenu({ deal, workspaceId, canConvert, onConvert }: Props) {
  const [target, setTarget] = useState<OpportunityStage | ''>('')
  const [scopeReference, setScopeReference] = useState(deal.scope_reference)
  const [lostReason, setLostReason] = useState('')
  const mutation = useTransitionOpportunity(workspaceId)

  const options = ALLOWED_TRANSITIONS[deal.stage] ?? []
  const needsScope = target === 'proposal'
  const needsLostReason = target === 'lost'
  // Winning creates a client and an onboarding project atomically, so it goes
  // through the conversion flow rather than a stage change (CRM07).
  const isConversion = target === 'won'

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!target) return
    if (isConversion) {
      if (canConvert) onConvert(deal)
      return
    }
    mutation.mutate({
      opportunityId: deal.id,
      targetStage: target,
      expectedVersion: deal.version,
      ...(needsScope ? { scopeReference } : {}),
      ...(needsLostReason ? { lostReason } : {}),
    })
  }

  return (
    <form onSubmit={handleSubmit} className={styles.stageForm}>
      <div className={styles.stageRow}>
        <label className={styles.stageSelect}>
          <span className="visually-hidden">Move to stage</span>
          <select
            value={target}
            onChange={(event) => setTarget(event.target.value as OpportunityStage | '')}
          >
            <option value="">Move to…</option>
            {options.map((stage) => (
              <option key={stage} value={stage}>
                {STAGE_LABELS[stage]}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          disabled={!target || (isConversion && !canConvert) || mutation.isPending}
          className={`${isConversion ? ui.primary : ui.secondary} ${ui.small}`}
          aria-label={`${isConversion ? 'Close as won' : 'Move stage'} for ${deal.service}`}
        >
          {mutation.isPending ? 'Moving…' : isConversion ? 'Close as won…' : 'Move'}
        </button>
      </div>

      {needsScope ? (
        <label className={ui.field}>
          <span>Scope reference</span>
          <TextInput
            limit={LIMITS.reference}
            type="text"
            required
            value={scopeReference}
            onChange={setScopeReference}
          />
        </label>
      ) : null}

      {needsLostReason ? (
        <label className={ui.field}>
          <span>Why was it lost?</span>
          <TextInput
            limit={LIMITS.reason}
            type="text"
            required
            value={lostReason}
            onChange={setLostReason}
          />
        </label>
      ) : null}

      {isConversion ? (
        <p className={ui.notice}>
          {canConvert
            ? 'Closing as won creates the client record and the onboarding project, and hands over to delivery.'
            : 'Closing as won creates a client and a delivery commitment, so a sales manager does it. Ask yours to close this deal.'}
        </p>
      ) : null}

      {mutation.isError ? (
        <p role="alert" className={ui.error}>
          {mutation.error instanceof ApiError && mutation.error.isVersionConflict
            ? 'This deal changed while you were looking at it. Reload before moving it.'
            : mutation.error instanceof ApiError
              ? mutation.error.message
              : 'The stage could not be changed.'}
        </p>
      ) : null}
    </form>
  )
}
