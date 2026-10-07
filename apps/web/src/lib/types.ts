/**
 * API response shapes.
 *
 * Generated types from the OpenAPI schema land in `api-types.ts` via
 * `pnpm api:types`. These hand-written interfaces cover what the Stage 1
 * screens use and are the ones the components import, so a schema regeneration
 * cannot silently reshape the UI.
 */

export type Role =
  | 'owner'
  | 'sales_manager'
  | 'sales_rep'
  | 'delivery_manager'
  | 'delivery_employee'
  | 'admin'
  | 'client'

export type LeadStatus =
  | 'new'
  | 'assigned'
  | 'contacting'
  | 'qualified'
  | 'nurture'
  | 'disqualified'

export type OpportunityStage =
  | 'discovery'
  | 'qualified'
  | 'proposal'
  | 'negotiation'
  | 'won'
  | 'lost'

export type TaskStatus = 'open' | 'blocked' | 'completed' | 'cancelled'

/**
 * How much is actually known about an activity (CRM04). The interface must
 * render these differently: a typed-in claim is not a verified call.
 */
export type EvidenceType = 'self_reported' | 'provider_confirmed'

export interface Workspace {
  id: string
  name: string
  slug: string
  time_zone: string
  calendar_version: number
  default_currency: string
}

export interface SessionContext {
  user_id: string
  email: string
  full_name: string
  workspace: Workspace
  membership_id: string
  role: Role
  team_id: string | null
  mfa_enrolled: boolean
  /** Drives navigation only. The server re-checks every call. */
  permissions: string[]
}

export interface Task {
  id: string
  title: string
  description: string
  kind: string
  status: TaskStatus
  owner: string | null
  owner_email: string | null
  origin: 'user' | 'rule' | 'import' | 'conversion'
  due_at: string
  /** Kept alongside due_at so a moved deadline stays visible (CRM05). */
  original_due_at: string
  reschedule_count: number
  is_overdue: boolean
  contact: string | null
  contact_name: string | null
  opportunity: string | null
  opportunity_service: string | null
  lead: string | null
  project: string | null
  milestone: string | null
  completed_at: string | null
  outcome: string
  stop_reason: string
  blocked_reason: string
  version: number
  created_at: string
}

export interface Notification {
  id: string
  title: string
  body: string
  /** Why this alert appeared (SVX-PRD-001 section 7.2). */
  cause: string
  state: string
  entity_type: string
  entity_id: string | null
  acknowledged_at: string | null
  created_at: string
}

export interface TodayResponse {
  overdue: Task[]
  due_now: Task[]
  upcoming: Task[]
  blocked: Task[]
  alerts: Notification[]
  server_time: string
  workspace_time_zone: string
}

export interface Contact {
  id: string
  display_name: string
  company: string | null
  company_name: string | null
  email: string
  phone: string
  phone_e164: string
  job_title: string
  source: string
  source_reference: string
  version: number
  created_at: string
  updated_at: string
}

export interface Lead {
  id: string
  contact: string
  contact_name: string
  owner: string | null
  owner_email: string | null
  source: string
  source_reference: string
  status: LeadStatus
  qualified_at: string | null
  nurture_review_at: string | null
  disqualified_reason: string
  /** What qualification established. Required to mark a lead qualified. */
  need: string
  fit: string
  notes: string
  version: number
  created_at: string
  updated_at: string
}

export interface Opportunity {
  id: string
  contact: string
  contact_name: string
  lead: string | null
  service: string
  stage: OpportunityStage
  owner: string | null
  owner_email: string | null
  /** Null means unknown. It is never zero-filled (section 4.1). */
  amount: string | null
  currency: string
  expected_close_on: string | null
  next_action_at: string | null
  stage_entered_at: string | null
  last_activity_at: string | null
  days_in_stage: number | null
  scope_reference: string
  commercial_reference: string
  closed_at: string | null
  lost_reason: string
  version: number
  created_at: string
  updated_at: string
}

export interface Activity {
  id: string
  contact: string
  contact_name: string
  opportunity: string | null
  lead: string | null
  kind: string
  outcome: string
  occurred_at: string
  recorded_at: string
  author: string | null
  author_email: string | null
  evidence_type: EvidenceType
  provider_reference: string
  source_url: string
  corrects: string | null
  /** The follow-up task created from this activity, if any. */
  follow_up_task: string | null
  created_at: string
}

export interface ImportBatch {
  id: string
  original_filename: string
  byte_size: number
  status: 'uploaded' | 'previewed' | 'committed' | 'processing' | 'completed' | 'failed'
  detected_headers: string[]
  column_mapping: Record<string, string>
  mapping_version: number
  default_country: string
  duplicate_policy: 'skip' | 'update'
  total_rows: number
  created_count: number
  duplicate_count: number
  invalid_count: number
  skipped_count: number
  counts_reconcile: boolean
  committed_at: string | null
  completed_at: string | null
  failure_reason: string
  version: number
  created_at: string
}

export interface ImportRowAssessment {
  row_number: number
  state: 'pending' | 'created' | 'duplicate' | 'invalid' | 'skipped'
  errors: string[]
  raw: Record<string, string>
}

export interface ImportPreview {
  batch: ImportBatch
  total_rows: number
  created: number
  duplicate: number
  invalid: number
  skipped: number
  /** Commit is blocked when false: an import must not lose rows (CRM02). */
  reconciles: boolean
  sample_rows: ImportRowAssessment[]
  preview_hash: string
}

export interface Paginated<T> {
  next: string | null
  previous: string | null
  results: T[]
}

// ---------------------------------------------------------------------------
// Delivery (CRM07, CRM08)
// ---------------------------------------------------------------------------

export type ClientStatus =
  | 'pending_handover'
  | 'accepted'
  | 'in_progress'
  | 'ready'
  | 'completed'

export interface Client {
  id: string
  display_name: string
  contact: string
  contact_name: string
  company: string | null
  originating_opportunity: string | null
  delivery_owner: string | null
  delivery_owner_email: string | null
  status: ClientStatus
  /** What sales promised, carried across at conversion. */
  accepted_scope: string
  exclusions: string
  commercial_reference: string
  promised_start_on: string | null
  promised_end_on: string | null
  handover_accepted_at: string | null
  handover_returned_at: string | null
  handover_returned_reason: string
  awaiting_handover: boolean
  version: number
  created_at: string
}

export type MilestoneStatus =
  | 'planned'
  | 'ready'
  | 'in_progress'
  | 'blocked'
  | 'in_review'
  | 'accepted'
  | 'cancelled'

export interface UnmetDependency {
  id: string
  name: string
  status: MilestoneStatus
}

export interface Milestone {
  id: string
  project: string
  name: string
  description: string
  sequence: number
  status: MilestoneStatus
  owner: string | null
  owner_email: string | null
  due_on: string | null
  is_overdue: boolean
  /** Submitted by an employee. Distinct from acceptance (CRM08). */
  submitted_at: string | null
  submitted_by: string | null
  evidence: string
  /** Accepted by an authorised reviewer. */
  accepted_at: string | null
  accepted_by: string | null
  acceptance_note: string
  blocked_reason: string
  blocked_next_owner: string | null
  dependency_override_reason: string
  cancelled_reason: string
  cancellation_impact: string
  /** Why acceptance is unavailable, so the UI can explain rather than just disable. */
  unmet_dependencies: UnmetDependency[]
  version: number
}

export interface Project {
  id: string
  client: string
  client_name: string
  originating_opportunity: string | null
  name: string
  description: string
  status: 'planned' | 'in_progress' | 'blocked' | 'completed' | 'cancelled'
  owner: string | null
  starts_on: string | null
  due_on: string | null
  completed_at: string | null
  milestones: Milestone[]
  version: number
  created_at: string
}

// ---------------------------------------------------------------------------
// Support (CRM09)
// ---------------------------------------------------------------------------

export type TicketState =
  | 'new'
  | 'triaged'
  | 'in_progress'
  | 'waiting_customer'
  | 'waiting_internal'
  | 'resolved'
  | 'closed'

export type TicketPriority = 'low' | 'normal' | 'high' | 'critical'

/** Internal is the default and must remain visually unmistakable (CRM09). */
export type CommentVisibility = 'internal' | 'client_visible'

export interface Ticket {
  id: string
  title: string
  description: string
  origin: 'client_request' | 'internal_issue'
  priority: TicketPriority
  state: TicketState
  is_open: boolean
  client: string | null
  client_name: string | null
  project: string | null
  milestone: string | null
  owner: string | null
  owner_email: string | null
  raised_by: string | null
  waiting_reason: string
  waiting_next_owner: string | null
  waiting_review_at: string | null
  waiting_overdue: boolean
  next_action: string
  resolution_note: string
  closure_test_result: string
  resolved_at: string | null
  closed_at: string | null
  reopen_count: number
  version: number
  created_at: string
}

export interface TicketComment {
  id: string
  ticket: string
  author: string | null
  author_email: string | null
  body: string
  visibility: CommentVisibility
  corrects: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// Reporting (CRM10, CRM11)
// ---------------------------------------------------------------------------

/**
 * Amounts per currency, never summed across them. `unknown_count` distinguishes
 * "no pipeline" from "pipeline we have not valued yet".
 */
export interface MoneyByCurrency {
  amounts: Record<string, string>
  record_count: number
  unknown_count: number
}

export interface OverviewReport {
  period: { start: string; end: string }
  scope: 'workspace' | 'own_records'
  /** Three separate measures. There is deliberately no combined total. */
  open_pipeline_value: MoneyByCurrency
  won_contract_value: MoneyByCurrency
  cash_received: {
    amounts: Record<string, string>
    record_count: number
    note: string
  }
  attention: Record<string, number>
  basis: {
    currencies_combined: boolean
    unknown_values_counted_as_zero: boolean
    filters: Record<string, string | null>
  }
}

export type ExceptionState =
  | 'open'
  | 'under_review'
  | 'disputed'
  | 'resolved'
  | 'dismissed'

export interface WorkException {
  id: string
  kind: string
  state: ExceptionState
  subject: string | null
  subject_email: string | null
  entity_type: string
  entity_id: string | null
  summary: string
  /** The observable facts, shown to the employee as well as the manager. */
  detail: Record<string, unknown>
  employee_explanation: string
  employee_responded_at: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  review_decision: string
  version: number
  created_at: string
}

// ---------------------------------------------------------------------------
// Identity (CRM01)
// ---------------------------------------------------------------------------

export type MembershipStatus = 'active' | 'suspended' | 'invited'

export interface Member {
  /** The membership id: what suspend and reinstate act on. */
  id: string
  /** The user id: what every owner and assignee field takes. */
  user: string
  email: string
  full_name: string
  role: Role
  status: MembershipStatus
  team: string | null
  team_name: string | null
  mfa_enrolled: boolean
  is_available_for_assignment: boolean
  suspended_at: string | null
  created_at: string
}

export interface InvitationCreated {
  invitation_id: string
  email: string
  role: Role
  expires_at: string
  /** Returned exactly once and never stored in plaintext. */
  token: string
}

// ---------------------------------------------------------------------------
// Delivery additions (CRM08)
// ---------------------------------------------------------------------------

export interface ScopeChange {
  id: string
  project: string
  milestone: string | null
  /** A scope change is new work; a defect is the original work not done. */
  kind: 'scope_change' | 'defect'
  description: string
  commercial_impact: string
  requested_by: string | null
  recorded_at: string
}

// ---------------------------------------------------------------------------
// Reporting additions (CRM11)
// ---------------------------------------------------------------------------

export interface CashReceipt {
  id: string
  client: string
  client_name: string
  opportunity: string | null
  amount: string
  currency: string
  received_at: string
  evidence_reference: string
  note: string
  recorded_by: string | null
  /** Set on a correction. The original receipt is never rewritten. */
  adjusts: string | null
  adjustment_reason: string
  created_at: string
}

export interface CoverageRatio {
  numerator: number
  denominator: number
  /** Null when there is nothing to measure: no coverage, good or bad. */
  percentage: number | null
  proposed_threshold: string
}

export interface OperationalReport {
  conversion_by_source: {
    period: { start: string; end: string }
    rows: {
      source: string
      leads: number
      qualified: number
      disqualified: number
      won: number
      won_per_lead: string
    }[]
    caveat: string
  }
  stage_aging: {
    stages: Record<
      string,
      { total: number; over_7_days: number; over_30_days: number; age_unknown: number }
    >
  }
  activity_evidence: {
    period: { start: string; end: string }
    self_reported: number
    provider_confirmed: number
    interpretation: string
  }
  delivery_health: {
    blocked_milestones: {
      id: string
      name: string
      project: string
      reason: string
      next_owner: string | null
    }[]
    dependency_overrides: number
    reopened_tickets: number
    tickets_waiting_past_review: number
  }
  pilot_measures: {
    ownership_coverage: CoverageRatio
    next_action_coverage: CoverageRatio
    handover_completeness: CoverageRatio
    note: string
  }
}

// ---------------------------------------------------------------------------
// Rules and alerts (CRM06)
// ---------------------------------------------------------------------------

export interface RuleAction {
  type: 'create_task' | 'assign_owner' | 'notify_owner' | 'notify_manager'
  channel?: 'in_app'
  title?: string
  body?: string
  task_kind?: string
  due_in?: RuleDelay
}

export interface RuleDelay {
  working_minutes?: number
  working_hours?: number
  working_days?: number
}

export interface RuleCondition {
  field: string
  op: 'eq' | 'neq' | 'in' | 'is_empty' | 'gt' | 'lt'
  value?: unknown
}

export interface Rule {
  id: string
  template: string
  name: string
  /** The rule's configuration version. Each edit writes a new one. */
  rule_version: number
  enabled: boolean
  paused: boolean
  paused_at: string | null
  trigger: string
  conditions: RuleCondition[]
  delay: RuleDelay
  actions: RuleAction[]
  limits: { max_actions: number; cooldown_minutes: number; max_chain_depth: number }
  explanation: string
  /** Row version for concurrency, distinct from rule_version. */
  version: number
  updated_at: string
}

export interface RuleSimulation {
  rule: string
  conditions: { field: string; op: string; value: unknown; actual: unknown; matched: boolean }[]
  would_fire: boolean
  proposed_actions: string[]
  delay: RuleDelay | null
  explanation: string
}

export interface Alert {
  id: string
  recipient: string
  recipient_email: string
  title: string
  body: string
  cause: string
  entity_type: string
  entity_id: string | null
  state: 'pending' | 'delivered' | 'acknowledged' | 'resolved' | 'failed'
  acknowledged_at: string | null
  resolved_at: string | null
  resolution_note: string
  created_at: string
}

export interface DuplicateSuggestion {
  id: string
  display_name: string
  email: string
  phone: string
  version: number
  reason: string
  /** False for a name similarity: a suggestion for review, never automatic. */
  is_definitive: boolean
}

export interface StageHistoryEntry {
  id: string
  from_stage: string
  to_stage: string
  actor: string | null
  actor_email: string | null
  changed_at: string
  reason: string
}
