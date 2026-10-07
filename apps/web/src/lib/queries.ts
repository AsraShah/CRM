/**
 * Query keys and hooks (SVX-TECH-001 section 2.1).
 *
 * Two rules the brief is explicit about:
 *
 * - Every key includes the workspace id, and caches are cleared on logout and
 *   on a workspace change. Otherwise a switch would briefly render the previous
 *   tenant's data from cache.
 * - No optimistic updates for stage changes, completion acceptance or financial
 *   records. A pending save must not look complete.
 */

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'

import { api, downloadFile, newIdempotencyKey } from './api'
import type {
  Activity,
  Alert,
  CashReceipt,
  Client,
  Contact,
  DuplicateSuggestion,
  ImportBatch,
  ImportPreview,
  InvitationCreated,
  Lead,
  Member,
  Milestone,
  OperationalReport,
  Opportunity,
  OverviewReport,
  Paginated,
  Project,
  Rule,
  RuleAction,
  RuleDelay,
  RuleSimulation,
  ScopeChange,
  SessionContext,
  StageHistoryEntry,
  Task,
  Ticket,
  TicketComment,
  TodayResponse,
  WorkException,
} from './types'

export const queryKeys = {
  session: ['session'] as const,
  today: (workspaceId: string) => [workspaceId, 'today'] as const,
  leads: (workspaceId: string, filters?: Record<string, string>) =>
    [workspaceId, 'leads', filters ?? {}] as const,
  opportunities: (workspaceId: string, filters?: Record<string, string>) =>
    [workspaceId, 'opportunities', filters ?? {}] as const,
  tasks: (workspaceId: string, filters?: Record<string, string>) =>
    [workspaceId, 'tasks', filters ?? {}] as const,
  imports: (workspaceId: string) => [workspaceId, 'imports'] as const,
  clients: (workspaceId: string, filters?: Record<string, string>) =>
    [workspaceId, 'clients', filters ?? {}] as const,
  projects: (workspaceId: string, filters?: Record<string, string>) =>
    [workspaceId, 'projects', filters ?? {}] as const,
  tickets: (workspaceId: string, filters?: Record<string, string>) =>
    [workspaceId, 'tickets', filters ?? {}] as const,
  ticketComments: (workspaceId: string, ticketId: string) =>
    [workspaceId, 'tickets', ticketId, 'comments'] as const,
  exceptions: (workspaceId: string, filters?: Record<string, string>) =>
    [workspaceId, 'exceptions', filters ?? {}] as const,
  overview: (workspaceId: string, period?: Record<string, string>) =>
    [workspaceId, 'reports', 'overview', period ?? {}] as const,
  operational: (workspaceId: string, period?: Record<string, string>) =>
    [workspaceId, 'reports', 'operational', period ?? {}] as const,
  members: (workspaceId: string) => [workspaceId, 'members'] as const,
  receipts: (workspaceId: string) => [workspaceId, 'receipts'] as const,
  activities: (workspaceId: string, filters?: Record<string, string>) =>
    [workspaceId, 'activities', filters ?? {}] as const,
  scopeChanges: (workspaceId: string, projectId: string) =>
    [workspaceId, 'projects', projectId, 'scope-changes'] as const,
}

/** Called on logout and on workspace change. */
export function clearWorkspaceCache(client: QueryClient): void {
  client.clear()
}

function toQueryString(filters?: Record<string, string>): string {
  if (!filters) return ''
  const entries = Object.entries(filters).filter(([, value]) => value !== '')
  if (entries.length === 0) return ''
  return `?${new URLSearchParams(entries).toString()}`
}

export function useSession() {
  return useQuery({
    queryKey: queryKeys.session,
    queryFn: () => api.get<SessionContext>('/me/'),
    // A 401 means "sign in", not "try again": retrying just delays the redirect.
    retry: false,
    staleTime: 5 * 60_000,
  })
}

export function useToday(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.today(workspaceId),
    queryFn: () => api.get<TodayResponse>('/today/'),
    enabled: Boolean(workspaceId),
    // The first screen of the day should reflect a colleague's reassignment
    // without a manual refresh, but not poll aggressively on a small server.
    refetchInterval: 120_000,
    refetchOnWindowFocus: true,
  })
}

export function useLeads(workspaceId: string, filters?: Record<string, string>) {
  return useQuery({
    queryKey: queryKeys.leads(workspaceId, filters),
    queryFn: () => api.get<Paginated<Lead>>(`/leads/${toQueryString(filters)}`),
    enabled: Boolean(workspaceId),
  })
}

export function useOpportunities(
  workspaceId: string,
  filters?: Record<string, string>,
) {
  return useQuery({
    queryKey: queryKeys.opportunities(workspaceId, filters),
    queryFn: () =>
      api.get<Paginated<Opportunity>>(`/opportunities/${toQueryString(filters)}`),
    enabled: Boolean(workspaceId),
  })
}

export interface CompleteTaskInput {
  taskId: string
  outcome: string
  expectedVersion: number
  nextActionTitle?: string
  nextActionDueAt?: string
  stopReason?: string
}

export function useCompleteTask(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: CompleteTaskInput) =>
      api.post<{ task: Task; next_task: Task | null }>(
        `/tasks/${input.taskId}/complete/`,
        {
          outcome: input.outcome,
          expected_version: input.expectedVersion,
          next_action_title: input.nextActionTitle ?? '',
          next_action_due_at: input.nextActionDueAt ?? null,
          stop_reason: input.stopReason ?? '',
        },
      ),
    // Deliberately not optimistic. The row updates once the server confirms,
    // so a failed save can never have looked successful.
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.today(workspaceId) })
      void client.invalidateQueries({ queryKey: [workspaceId, 'tasks'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'opportunities'] })
    },
  })
}

export interface TransitionInput {
  opportunityId: string
  targetStage: string
  expectedVersion: number
  reason?: string
  scopeReference?: string
  lostReason?: string
}

export function useTransitionOpportunity(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: TransitionInput) =>
      api.post<Opportunity>(`/opportunities/${input.opportunityId}/transition/`, {
        target_stage: input.targetStage,
        expected_version: input.expectedVersion,
        reason: input.reason ?? '',
        scope_reference: input.scopeReference ?? '',
        lost_reason: input.lostReason ?? '',
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'opportunities'] })
      void client.invalidateQueries({ queryKey: queryKeys.today(workspaceId) })
    },
  })
}

export function useRescheduleTask(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      taskId: string
      newDueAt: string
      reason: string
      expectedVersion: number
    }) =>
      api.post<Task>(`/tasks/${input.taskId}/reschedule/`, {
        new_due_at: input.newDueAt,
        reason: input.reason,
        expected_version: input.expectedVersion,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.today(workspaceId) })
    },
  })
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

export function useUploadImport(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api.post<ImportBatch>('/imports/', form)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.imports(workspaceId) })
    },
  })
}

export function usePreviewImport() {
  return useMutation({
    mutationFn: (input: {
      batchId: string
      columnMapping: Record<string, string>
      defaultCountry: string
      duplicatePolicy: 'skip' | 'update'
    }) =>
      api.post<ImportPreview>(`/imports/${input.batchId}/preview/`, {
        column_mapping: input.columnMapping,
        default_country: input.defaultCountry,
        duplicate_policy: input.duplicatePolicy,
      }),
  })
}

export function useCommitImport(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      batchId: string
      previewHash: string
      expectedVersion: number
    }) =>
      api.post<ImportBatch>(
        `/imports/${input.batchId}/commit/`,
        { preview_hash: input.previewHash, expected_version: input.expectedVersion },
        // A commit the user retries after a timeout must resume the same
        // batch, not import every row twice.
        newIdempotencyKey(),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.imports(workspaceId) })
      void client.invalidateQueries({ queryKey: [workspaceId, 'leads'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Delivery (CRM07, CRM08)
// ---------------------------------------------------------------------------

export function useClients(workspaceId: string, filters?: Record<string, string>) {
  return useQuery({
    queryKey: queryKeys.clients(workspaceId, filters),
    queryFn: () => api.get<Paginated<Client>>(`/clients/${toQueryString(filters)}`),
    enabled: Boolean(workspaceId),
  })
}

export function useProjects(workspaceId: string, filters?: Record<string, string>) {
  return useQuery({
    queryKey: queryKeys.projects(workspaceId, filters),
    queryFn: () => api.get<Paginated<Project>>(`/projects/${toQueryString(filters)}`),
    enabled: Boolean(workspaceId),
  })
}

/**
 * Convert a won deal.
 *
 * Carries an idempotency key because this is the most expensive request in the
 * product to duplicate: without one, a retry after a timeout could create a
 * second client and a second onboarding project (CRM07).
 */
export function useConvertOpportunity(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      opportunityId: string
      expectedVersion: number
      acceptedScopeEvidence: string
      commercialReference: string
      deliveryOwnerId: string
      exclusions?: string
      promisedStartOn?: string
      promisedEndOn?: string
    }) =>
      api.post<{
        opportunity: Opportunity
        client: Client
        project: Project
        was_existing: boolean
      }>(
        `/opportunities/${input.opportunityId}/convert/`,
        {
          expected_version: input.expectedVersion,
          accepted_scope_evidence: input.acceptedScopeEvidence,
          commercial_reference: input.commercialReference,
          delivery_owner_id: input.deliveryOwnerId,
          exclusions: input.exclusions ?? '',
          promised_start_on: input.promisedStartOn ?? null,
          promised_end_on: input.promisedEndOn ?? null,
        },
        newIdempotencyKey(),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'opportunities'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'clients'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'projects'] })
    },
  })
}

export function useHandoverDecision(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      clientId: string
      expectedVersion: number
      accept: boolean
      note?: string
      missingInformation?: string
    }) =>
      input.accept
        ? api.post<Client>(`/clients/${input.clientId}/accept-handover/`, {
            expected_version: input.expectedVersion,
            note: input.note ?? '',
          })
        : api.post<Client>(`/clients/${input.clientId}/return-handover/`, {
            expected_version: input.expectedVersion,
            missing_information: input.missingInformation ?? '',
          }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'clients'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'projects'] })
    },
  })
}

export function useMilestoneAction(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      milestoneId: string
      action: 'start' | 'submit' | 'accept' | 'reopen' | 'block' | 'cancel' | 'send-back'
      expectedVersion: number
      evidence?: string
      impact?: string
      note?: string
      reason?: string
      blockedReason?: string
      nextOwnerId?: string
      overrideDependencies?: boolean
      overrideReason?: string
    }) => {
      const body: Record<string, unknown> = { expected_version: input.expectedVersion }
      if (input.action === 'submit') body.evidence = input.evidence ?? ''
      if (input.action === 'accept') {
        body.note = input.note ?? ''
        body.override_dependencies = input.overrideDependencies ?? false
        body.override_reason = input.overrideReason ?? ''
      }
      if (input.action === 'reopen' || input.action === 'send-back') {
        body.reason = input.reason ?? ''
      }
      if (input.action === 'cancel') {
        body.reason = input.reason ?? ''
        body.impact = input.impact ?? ''
      }
      if (input.action === 'block') {
        body.blocked_reason = input.blockedReason ?? ''
        body.next_owner_id = input.nextOwnerId
      }
      return api.post<Milestone>(
        `/milestones/${input.milestoneId}/${input.action}/`,
        body,
      )
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'projects'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'tasks'] })
    },
  })
}

export function useMilestoneTasks(workspaceId: string, milestoneId: string) {
  return useQuery({
    queryKey: queryKeys.tasks(workspaceId, { milestone: milestoneId }),
    queryFn: () => api.get<Paginated<Task>>(`/tasks/?milestone=${milestoneId}`),
    enabled: Boolean(workspaceId && milestoneId),
  })
}

export function useAddDependency(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { milestoneId: string; dependsOn: string }) =>
      api.post<Milestone>(`/milestones/${input.milestoneId}/dependencies/`, {
        depends_on: input.dependsOn,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'projects'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Support (CRM09)
// ---------------------------------------------------------------------------

export function useTickets(workspaceId: string, filters?: Record<string, string>) {
  return useQuery({
    queryKey: queryKeys.tickets(workspaceId, filters),
    queryFn: () => api.get<Paginated<Ticket>>(`/tickets/${toQueryString(filters)}`),
    enabled: Boolean(workspaceId),
  })
}

export function useTicketComments(workspaceId: string, ticketId: string | null) {
  return useQuery({
    queryKey: queryKeys.ticketComments(workspaceId, ticketId ?? ''),
    queryFn: () => api.get<TicketComment[]>(`/tickets/${ticketId}/comments/`),
    enabled: Boolean(workspaceId && ticketId),
  })
}

export function useAddTicketComment(workspaceId: string, ticketId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { body: string; visibility: 'internal' | 'client_visible' }) =>
      api.post<TicketComment>(`/tickets/${ticketId}/comments/`, input),
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: queryKeys.ticketComments(workspaceId, ticketId),
      })
    },
  })
}

export function useTicketTransition(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      ticketId: string
      targetState: string
      expectedVersion: number
      reason?: string
      waitingReason?: string
      waitingNextOwnerId?: string
      waitingReviewAt?: string
      resolutionNote?: string
      closureTestResult?: string
    }) =>
      api.post<Ticket>(`/tickets/${input.ticketId}/transition/`, {
        target_state: input.targetState,
        expected_version: input.expectedVersion,
        reason: input.reason ?? '',
        waiting_reason: input.waitingReason ?? '',
        waiting_next_owner_id: input.waitingNextOwnerId ?? null,
        waiting_review_at: input.waitingReviewAt ?? null,
        resolution_note: input.resolutionNote ?? '',
        closure_test_result: input.closureTestResult ?? '',
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'tickets'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Reporting (CRM10, CRM11)
// ---------------------------------------------------------------------------

export function useOverview(workspaceId: string, period?: Record<string, string>) {
  return useQuery({
    queryKey: queryKeys.overview(workspaceId, period),
    queryFn: () => api.get<OverviewReport>(`/reports/overview/${toQueryString(period)}`),
    enabled: Boolean(workspaceId),
    // Keep the last period on screen while a new one loads, so the period
    // picker is not unmounted mid-edit.
    placeholderData: keepPreviousData,
  })
}

export function useExceptions(workspaceId: string, filters?: Record<string, string>) {
  return useQuery({
    queryKey: queryKeys.exceptions(workspaceId, filters),
    queryFn: () =>
      api.get<Paginated<WorkException>>(`/exceptions/${toQueryString(filters)}`),
    enabled: Boolean(workspaceId),
  })
}

export function useExceptionResponse(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      exceptionId: string
      mode: 'explain' | 'review'
      explanation?: string
      dispute?: boolean
      decision?: string
      dismiss?: boolean
      /** Guards against two managers deciding the same exception at once. */
      expectedVersion?: number
    }) =>
      api.post<WorkException>(
        `/exceptions/${input.exceptionId}/${input.mode}/`,
        input.mode === 'explain'
          ? { explanation: input.explanation ?? '', dispute: input.dispute ?? false }
          : {
              decision: input.decision ?? '',
              dismiss: input.dismiss ?? false,
              ...(input.expectedVersion ? { expected_version: input.expectedVersion } : {}),
            },
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'exceptions'] })
    },
  })
}

export function useOperationalReport(
  workspaceId: string,
  period?: Record<string, string>,
) {
  return useQuery({
    queryKey: queryKeys.operational(workspaceId, period),
    queryFn: () =>
      api.get<OperationalReport>(`/reports/operational/${toQueryString(period)}`),
    enabled: Boolean(workspaceId),
    placeholderData: keepPreviousData,
  })
}

export function useReceipts(workspaceId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.receipts(workspaceId),
    queryFn: () => api.get<Paginated<CashReceipt>>('/receipts/'),
    enabled: Boolean(workspaceId) && enabled,
  })
}

/**
 * Record money received (CRM11).
 *
 * The endpoint does not deduplicate, so the form disables itself while the save
 * is in flight: a double-submitted receipt would overstate cash.
 */
export function useRecordReceipt(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      client: string
      amount: string
      currency: string
      receivedAt: string
      evidenceReference: string
      note?: string
      adjusts?: string
      adjustmentReason?: string
    }) =>
      api.post<CashReceipt>(
        '/receipts/',
        {
          client: input.client,
          amount: input.amount,
          currency: input.currency,
          received_at: input.receivedAt,
          evidence_reference: input.evidenceReference,
          note: input.note ?? '',
          adjusts: input.adjusts ?? null,
          adjustment_reason: input.adjustmentReason ?? '',
        },
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.receipts(workspaceId) })
      void client.invalidateQueries({ queryKey: [workspaceId, 'reports'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Identity and administration (CRM01, CRM13)
// ---------------------------------------------------------------------------

/**
 * The workspace roster. Every owner picker reads from here, so it only ever
 * offers people in this workspace.
 */
export function useMembers(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.members(workspaceId),
    queryFn: () => api.get<Paginated<Member>>('/memberships/'),
    enabled: Boolean(workspaceId),
    staleTime: 5 * 60_000,
  })
}

export function useInviteMember(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { email: string; role: string }) =>
      api.post<InvitationCreated>('/memberships/invite/', input),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.members(workspaceId) })
    },
  })
}

export function useMembershipAction(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      membershipId: string
      action: 'suspend' | 'reinstate'
      reason?: string
    }) =>
      api.post<Member>(
        `/memberships/${input.membershipId}/${input.action}/`,
        input.action === 'suspend' ? { reason: input.reason ?? '' } : {},
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.members(workspaceId) })
    },
  })
}

/** Owner-only, MFA-gated and audited on the server (CRM13). */
export function useExportWorkspace() {
  return useMutation({
    mutationFn: () => downloadFile('/export/'),
  })
}

// ---------------------------------------------------------------------------
// Sales actions (CRM03, CRM04)
// ---------------------------------------------------------------------------

/** Raised when the contact was created but the lead was not. */
export class LeadAfterContactError extends Error {
  readonly contactId: string
  override readonly cause: unknown

  constructor(contactId: string, cause: unknown) {
    super(cause instanceof Error ? cause.message : 'The lead could not be created.')
    this.name = 'LeadAfterContactError'
    this.contactId = contactId
    this.cause = cause
  }
}

/**
 * Add a lead for a new person.
 *
 * A lead always points at a contact, so this creates the contact first and then
 * the lead. If the second call fails the contact already exists, so the error
 * carries its id and the retry reuses it rather than creating the person twice.
 */
export function useCreateLead(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (input: {
      displayName: string
      email: string
      phone: string
      jobTitle: string
      source: string
      sourceReference: string
      notes: string
      owner: string
      existingContactId?: string
    }) => {
      const contactId =
        input.existingContactId ??
        (
          await api.post<Contact>('/contacts/', {
            display_name: input.displayName,
            email: input.email,
            phone: input.phone,
            job_title: input.jobTitle,
            source: input.source,
            source_reference: input.sourceReference,
          })
        ).id
      try {
        return await api.post<Lead>('/leads/', {
          contact: contactId,
          owner: input.owner || null,
          source: input.source,
          source_reference: input.sourceReference,
          notes: input.notes,
        })
      } catch (error) {
        throw new LeadAfterContactError(contactId, error)
      }
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'leads'] })
    },
  })
}

export function useLeadStatus(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      leadId: string
      status: string
      expectedVersion: number
      nurtureReviewAt?: string
      disqualifiedReason?: string
      need?: string
      fit?: string
    }) =>
      api.post<Lead>(`/leads/${input.leadId}/status/`, {
        status: input.status,
        expected_version: input.expectedVersion,
        nurture_review_at: input.nurtureReviewAt ?? null,
        disqualified_reason: input.disqualifiedReason ?? '',
        need: input.need ?? '',
        fit: input.fit ?? '',
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'leads'] })
    },
  })
}

export function useAssignLead(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      leadId: string
      owner: string
      expectedVersion: number
      reason?: string
    }) =>
      api.post<Lead>(`/leads/${input.leadId}/assign/`, {
        owner: input.owner,
        expected_version: input.expectedVersion,
        reason: input.reason ?? '',
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'leads'] })
      void client.invalidateQueries({ queryKey: queryKeys.today(workspaceId) })
    },
  })
}

export function useCreateOpportunity(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      contact: string
      lead?: string
      service: string
      owner?: string
      amount?: string
      currency?: string
      expectedCloseOn?: string
    }) =>
      api.post<Opportunity>('/opportunities/', {
        contact: input.contact,
        lead: input.lead ?? null,
        service: input.service,
        owner: input.owner || null,
        // Blank stays null: an unagreed value is unknown, not zero.
        amount: input.amount ? input.amount : null,
        currency: input.currency ?? '',
        expected_close_on: input.expectedCloseOn || null,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'opportunities'] })
    },
  })
}

export function useActivities(workspaceId: string, filters: Record<string, string>) {
  return useQuery({
    queryKey: queryKeys.activities(workspaceId, filters),
    queryFn: () => api.get<Paginated<Activity>>(`/activities/${toQueryString(filters)}`),
    enabled: Boolean(workspaceId),
  })
}

/**
 * Log a call, email, meeting or LinkedIn touch (CRM04).
 *
 * There is deliberately no evidence field: the server labels every manual entry
 * self-reported, whatever a client might send.
 */
export function useRecordActivity(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      contact: string
      kind: string
      occurredAt: string
      outcome: string
      lead?: string
      opportunity?: string
      followUpTitle?: string
      followUpDueAt?: string
    }) =>
      api.post<Activity>(
        '/activities/',
        {
          contact: input.contact,
          kind: input.kind,
          occurred_at: input.occurredAt,
          outcome: input.outcome,
          lead: input.lead ?? null,
          opportunity: input.opportunity ?? null,
          // The next action agreed in this interaction (CRM04).
          follow_up_title: input.followUpTitle ?? '',
          follow_up_due_at: input.followUpDueAt || null,
        },
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'activities'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'opportunities'] })
      void client.invalidateQueries({ queryKey: queryKeys.today(workspaceId) })
    },
  })
}

// ---------------------------------------------------------------------------
// Work (CRM05)
// ---------------------------------------------------------------------------

export function useCreateTask(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      title: string
      dueAt: string
      kind: string
      description: string
      owner?: string
      milestone?: string
    }) =>
      api.post<Task>('/tasks/', {
        title: input.title,
        due_at: input.dueAt,
        kind: input.kind,
        description: input.description,
        owner: input.owner || null,
        milestone: input.milestone || null,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.today(workspaceId) })
      void client.invalidateQueries({ queryKey: [workspaceId, 'tasks'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Delivery additions (CRM08, CRM09)
// ---------------------------------------------------------------------------

export function useScopeChanges(workspaceId: string, projectId: string) {
  return useQuery({
    queryKey: queryKeys.scopeChanges(workspaceId, projectId),
    queryFn: () => api.get<ScopeChange[]>(`/projects/${projectId}/scope-changes/`),
    enabled: Boolean(workspaceId && projectId),
  })
}

export function useRecordScopeChange(workspaceId: string, projectId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      kind: 'scope_change' | 'defect'
      description: string
      commercialImpact: string
      milestoneId?: string
    }) =>
      api.post<ScopeChange>(`/projects/${projectId}/scope-changes/`, {
        kind: input.kind,
        description: input.description,
        commercial_impact: input.commercialImpact,
        milestone_id: input.milestoneId || null,
      }),
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: queryKeys.scopeChanges(workspaceId, projectId),
      })
    },
  })
}

export function useCreateTicket(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      title: string
      description: string
      origin: 'client_request' | 'internal_issue'
      priority: string
      client?: string
      ownerId?: string
    }) =>
      api.post<Ticket>(
        '/tickets/',
        {
          title: input.title,
          description: input.description,
          origin: input.origin,
          priority: input.priority,
          client: input.client || null,
          owner_id: input.ownerId || null,
        },
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'tickets'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Editing (CRM03). Each sends the version it was editing; a 409 means somebody
// else saved first, and the form says so rather than retrying.
// ---------------------------------------------------------------------------

export function useContact(workspaceId: string, contactId: string) {
  return useQuery({
    queryKey: [workspaceId, 'contacts', contactId] as const,
    queryFn: () => api.get<Contact>(`/contacts/${contactId}/`),
    enabled: Boolean(workspaceId && contactId),
  })
}

export function useUpdateContact(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { contactId: string; expectedVersion: number; changes: Record<string, string> }) =>
      api.patch<Contact>(`/contacts/${input.contactId}/`, {
        expected_version: input.expectedVersion,
        ...input.changes,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'contacts'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'leads'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'opportunities'] })
    },
  })
}

export function useUpdateLead(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { leadId: string; expectedVersion: number; changes: Record<string, string> }) =>
      api.patch<Lead>(`/leads/${input.leadId}/`, {
        expected_version: input.expectedVersion,
        ...input.changes,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'leads'] })
    },
  })
}

export function useUpdateOpportunity(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      opportunityId: string
      expectedVersion: number
      changes: Record<string, string | null>
    }) =>
      api.patch<Opportunity>(`/opportunities/${input.opportunityId}/`, {
        expected_version: input.expectedVersion,
        ...input.changes,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'opportunities'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'reports'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Rules and alerts (CRM06)
// ---------------------------------------------------------------------------

export function useRules(workspaceId: string, enabled = true) {
  return useQuery({
    queryKey: [workspaceId, 'rules'] as const,
    queryFn: () => api.get<Rule[]>('/rules/'),
    enabled: Boolean(workspaceId) && enabled,
  })
}

export function useRuleAction(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      ruleId: string
      action: 'enable' | 'disable' | 'pause' | 'resume'
      expectedVersion: number
    }) =>
      api.post<Rule>(`/rules/${input.ruleId}/${input.action}/`, {
        expected_version: input.expectedVersion,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'rules'] })
    },
  })
}

export function useUpdateRule(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      ruleId: string
      expectedVersion: number
      changes: { delay?: RuleDelay | null; actions?: RuleAction[]; explanation?: string }
    }) =>
      api.patch<Rule>(`/rules/${input.ruleId}/`, {
        expected_version: input.expectedVersion,
        ...input.changes,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'rules'] })
    },
  })
}

export function useSimulateRule() {
  return useMutation({
    mutationFn: (input: { ruleId: string; entityType: string; entityId: string }) =>
      api.post<RuleSimulation>(`/rules/${input.ruleId}/simulate/`, {
        entity_type: input.entityType,
        entity_id: input.entityId,
      }),
  })
}

export function useInstallCatalogue(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<{ installed: number }>('/rules/install-catalogue/'),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'rules'] })
    },
  })
}

export function useAlertAction(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { alertId: string; action: 'acknowledge' | 'resolve'; note?: string }) =>
      api.post<Alert>(
        `/alerts/${input.alertId}/${input.action}/`,
        input.action === 'resolve' ? { note: input.note ?? '' } : undefined,
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.today(workspaceId) })
      void client.invalidateQueries({ queryKey: [workspaceId, 'alerts'] })
    },
  })
}

export function useTicketNextAction(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { ticketId: string; expectedVersion: number; nextAction: string }) =>
      api.post<Ticket>(`/tickets/${input.ticketId}/next-action/`, {
        expected_version: input.expectedVersion,
        next_action: input.nextAction,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'tickets'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Corrections and merging (CRM04, CRM02)
// ---------------------------------------------------------------------------

/** Append a correction. The original entry is never rewritten. */
export function useCorrectActivity(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { activityId: string; outcome: string; reason: string }) =>
      api.post<Activity>(`/activities/${input.activityId}/correct/`, {
        outcome: input.outcome,
        reason: input.reason,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'activities'] })
    },
  })
}

export function useDuplicates(workspaceId: string, contactId: string, enabled: boolean) {
  return useQuery({
    queryKey: [workspaceId, 'contacts', contactId, 'duplicates'] as const,
    queryFn: () => api.get<DuplicateSuggestion[]>(`/contacts/${contactId}/duplicates/`),
    enabled: Boolean(workspaceId && contactId) && enabled,
  })
}

export function useMergeContact(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      survivorId: string
      duplicateId: string
      survivorVersion: number
      duplicateVersion: number
      reason: string
    }) =>
      api.post<Contact>(`/contacts/${input.survivorId}/merge/`, {
        duplicate_id: input.duplicateId,
        survivor_version: input.survivorVersion,
        duplicate_version: input.duplicateVersion,
        reason: input.reason,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'contacts'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'leads'] })
      void client.invalidateQueries({ queryKey: [workspaceId, 'activities'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Customer record (CRM07: the client workspace)
// ---------------------------------------------------------------------------

export function useClient(workspaceId: string, clientId: string) {
  return useQuery({
    queryKey: [workspaceId, 'clients', clientId] as const,
    queryFn: () => api.get<Client>(`/clients/${clientId}/`),
    enabled: Boolean(workspaceId && clientId),
  })
}

export function useOpportunity(workspaceId: string, opportunityId: string | null) {
  return useQuery({
    queryKey: [workspaceId, 'opportunities', 'one', opportunityId ?? ''] as const,
    queryFn: () => api.get<Opportunity>(`/opportunities/${opportunityId}/`),
    enabled: Boolean(workspaceId && opportunityId),
    retry: false,
  })
}

export function useStageHistory(workspaceId: string, opportunityId: string | null) {
  return useQuery({
    queryKey: [workspaceId, 'opportunities', 'history', opportunityId ?? ''] as const,
    queryFn: () => api.get<StageHistoryEntry[]>(`/opportunities/${opportunityId}/stage-history/`),
    enabled: Boolean(workspaceId && opportunityId),
    retry: false,
  })
}

export interface MeasureRecords {
  measure: string
  count: number
  shown: number
  period: { start: string; end: string }
  records: { type: string; id: string; label: string; detail: string }[]
}

/** The rows behind one dashboard figure (CRM11). */
export function useMeasureRecords(
  workspaceId: string,
  measure: string | null,
  period: Record<string, string>,
) {
  return useQuery({
    queryKey: [workspaceId, 'reports', 'records', measure ?? '', period] as const,
    queryFn: () =>
      api.get<MeasureRecords>(
        `/reports/records/${toQueryString({ measure: measure ?? '', ...period })}`,
      ),
    enabled: Boolean(workspaceId && measure),
  })
}

// ---------------------------------------------------------------------------
// Optional AI assistance (CRM12). Disabled unless the server says otherwise.
// ---------------------------------------------------------------------------

export interface AiDraft {
  id: string
  purpose: 'summarise_notes' | 'draft_follow_up'
  output: {
    summary?: string
    suggested_next_action?: string
    subject?: string
    body?: string
    source_ids?: string[]
  }
  uncertainties: string[]
  source_ids: string[]
  accepted_at: string | null
  rejected_at: string | null
  validation_error: string
}

export function useAiStatus(workspaceId: string) {
  return useQuery({
    queryKey: [workspaceId, 'ai', 'status'] as const,
    queryFn: () => api.get<{ enabled: boolean; remaining_usd: string }>('/ai/budget/'),
    enabled: Boolean(workspaceId),
    staleTime: 60_000,
    retry: false,
  })
}

export function useRequestDraft() {
  return useMutation({
    mutationFn: (input: {
      purpose: AiDraft['purpose']
      activityIds: string[]
      opportunityId?: string
    }) =>
      api.post<{ draft: AiDraft; sources: Activity[]; review_note: string }>(
        '/ai/drafts/',
        {
          purpose: input.purpose,
          activity_ids: input.activityIds,
          opportunity_id: input.opportunityId ?? null,
          // Binds a retry to the same budget reservation.
          request_key: newIdempotencyKey(),
        },
      ),
  })
}

export function useDraftDecision() {
  return useMutation({
    mutationFn: (input: { draftId: string; accepted: boolean; reason?: string }) =>
      api.post<AiDraft>(`/ai/drafts/${input.draftId}/decision/`, {
        accepted: input.accepted,
        reason: input.reason ?? '',
      }),
  })
}
