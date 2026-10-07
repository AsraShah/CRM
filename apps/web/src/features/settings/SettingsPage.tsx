import { CalendarDays, Copy, Download, ShieldCheck, ShieldOff, UserPlus, Users, Zap } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext } from 'react-router'

import { CalendarPanel } from './CalendarPanel'
import { RulesPanel } from './RulesPanel'
import styles from './SettingsPage.module.css'

import { TextArea, TextInput } from '@/components/fields'
import { Dialog } from '@/components/Dialog'
import { ErrorNotice } from '@/components/ErrorNotice'
import { PageHeader, Person } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { LIMITS } from '@/lib/limits'
import { formatDate, formatDateTime } from '@/lib/dates'
import {
  useExportWorkspace,
  useInviteMember,
  useMembers,
  useMembershipAction,
} from '@/lib/queries'
import type { InvitationCreated, Member, Role, SessionContext } from '@/lib/types'

export const ROLE_LABELS: Record<Role, string> = {
  owner: 'Owner',
  admin: 'Administrator',
  sales_manager: 'Sales manager',
  sales_rep: 'Sales representative',
  delivery_manager: 'Delivery manager',
  delivery_employee: 'Delivery employee',
  client: 'Client',
}

/** Roles an administrator may invite. Client users arrive with Release 3. */
const INVITABLE: Role[] = [
  'sales_rep',
  'sales_manager',
  'delivery_employee',
  'delivery_manager',
  'admin',
  'owner',
]

/**
 * Team and workspace settings (CRM01, CRM13).
 *
 * Kept out of the primary navigation for most roles — "do not present every
 * configuration option to every employee" (SVX-PRD-001 section 1.3).
 *
 * Suspension revokes access on the next request and keeps every record the
 * person created. There is no delete, deliberately.
 */
export function SettingsPage() {
  const session = useOutletContext<SessionContext>()
  const can = (permission: string) => session.permissions.includes(permission)
  const privileged = session.role === 'owner' || session.role === 'admin'

  const sections = [
    { id: 'members', label: 'Members', icon: <Users size={16} aria-hidden="true" />, show: true },
    {
      id: 'invite',
      label: 'Invitations',
      icon: <UserPlus size={16} aria-hidden="true" />,
      show: can('invitation.manage'),
    },
    { id: 'rules', label: 'Rules', icon: <Zap size={16} aria-hidden="true" />, show: can('rule.manage') },
    {
      id: 'calendar',
      label: 'Working calendar',
      icon: <CalendarDays size={16} aria-hidden="true" />,
      show: can('workspace.configure'),
    },
    {
      id: 'export',
      label: 'Data export',
      icon: <Download size={16} aria-hidden="true" />,
      show: can('workspace.export'),
    },
  ].filter((s) => s.show)

  return (
    <div className={ui.page}>
      <PageHeader
        eyebrow="Administration"
        title="Settings"
        subtitle={`${session.workspace.name} · time zone ${session.workspace.time_zone} · default currency ${session.workspace.default_currency}`}
      />

      {privileged && !session.mfa_enrolled ? (
        <p className={ui.warning}>
          Managing the team, recording receipts and exporting data stay locked
          until you{' '}
          <a href="/accounts/2fa/totp/activate/">set up two-factor authentication</a>.
        </p>
      ) : null}

      <div className={sections.length > 1 ? styles.layout : undefined}>
        {sections.length > 1 ? (
          <nav aria-label="Settings sections" className={styles.nav}>
            {sections.map((s) => (
              <a key={s.id} href={`#${s.id}`} className={styles.navLink}>
                {s.icon}
                {s.label}
              </a>
            ))}
          </nav>
        ) : null}

        <div className={styles.sections}>
          <div id="members" className={styles.anchor}>
            <Roster session={session} canManage={can('membership.manage')} />
          </div>
          {can('invitation.manage') ? (
            <div id="invite" className={styles.anchor}>
              <InviteForm session={session} />
            </div>
          ) : null}
          {can('rule.manage') ? (
            <div id="rules" className={styles.anchor}>
              <RulesPanel session={session} />
            </div>
          ) : null}
          {can('workspace.configure') ? (
            <div id="calendar" className={styles.anchor}>
              <CalendarPanel session={session} />
            </div>
          ) : null}
          {can('workspace.export') ? (
            <div id="export" className={styles.anchor}>
              <ExportPanel />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function Roster({ session, canManage }: { session: SessionContext; canManage: boolean }) {
  const { data, isLoading, isError } = useMembers(session.workspace.id)
  const [acting, setActing] = useState<Member | null>(null)

  return (
    <section aria-labelledby="roster-heading" className={`${ui.card} ${ui.cardFlush}`}>
      <div className={ui.panelHeader}>
        <h2 id="roster-heading">
          Team
          {data ? <span className={ui.count}> ({data.results.length})</span> : null}
        </h2>
      </div>
      {isLoading ? (
        <p role="status" className={ui.panelEmpty}>Loading the team…</p>
      ) : isError || !data ? (
        <p role="alert" className={ui.panelEmpty}>The team could not be loaded.</p>
      ) : (
        <div className={styles.flushTable}>
          <table className={ui.table}>
            <caption className="visually-hidden">
              Workspace members with role, status and two-factor enrolment.
            </caption>
            <thead>
              <tr>
                <th scope="col">Person</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Two-factor</th>
                {canManage ? <th scope="col">Access</th> : null}
              </tr>
            </thead>
            <tbody>
              {data.results.map((member) => (
                <tr key={member.id}>
                  <th scope="row">
                    <Person
                      name={member.full_name || member.email}
                      detail={member.full_name ? member.email : undefined}
                    />
                  </th>
                  <td>
                    {ROLE_LABELS[member.role]}
                    {member.team_name ? (
                      <div className={ui.muted}>{member.team_name}</div>
                    ) : null}
                  </td>
                  <td>
                    {member.status === 'suspended' ? (
                      <span className={ui.pillDanger}>
                        Suspended {formatDate(member.suspended_at)}
                      </span>
                    ) : member.status === 'invited' ? (
                      <span className={ui.pillWarning}>Invited</span>
                    ) : (
                      <span className={ui.pillSuccess}>Active</span>
                    )}
                  </td>
                  <td>
                    {member.mfa_enrolled ? (
                      <span className={styles.mfaOn}>
                        <ShieldCheck size={15} aria-hidden="true" /> Enrolled
                      </span>
                    ) : (
                      <span className={styles.mfaOff}>
                        <ShieldOff size={15} aria-hidden="true" /> Not set up
                      </span>
                    )}
                  </td>
                  {canManage ? (
                    <td>
                      {member.user === session.user_id ? (
                        <span className={ui.muted}>You</span>
                      ) : member.status === 'suspended' ? (
                        <button
                          type="button"
                          className={`${ui.secondary} ${ui.small}`}
                          aria-label={`Reinstate ${member.email}`}
                          onClick={() => setActing(member)}
                        >
                          Reinstate
                        </button>
                      ) : member.status === 'active' ? (
                        <button
                          type="button"
                          className={`${ui.danger} ${ui.small}`}
                          aria-label={`Suspend ${member.email}`}
                          onClick={() => setActing(member)}
                        >
                          Suspend
                        </button>
                      ) : null}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {acting ? (
        <AccessDialog
          member={acting}
          workspaceId={session.workspace.id}
          onClose={() => setActing(null)}
        />
      ) : null}
    </section>
  )
}

function AccessDialog({
  member,
  workspaceId,
  onClose,
}: {
  member: Member
  workspaceId: string
  onClose: () => void
}) {
  const mutation = useMembershipAction(workspaceId)
  const [reason, setReason] = useState('')
  const suspending = member.status !== 'suspended'

  function submit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate(
      {
        membershipId: member.id,
        action: suspending ? 'suspend' : 'reinstate',
        reason,
      },
      { onSuccess: onClose },
    )
  }

  return (
    <Dialog
      title={suspending ? 'Suspend access' : 'Reinstate access'}
      context={member.email}
      onClose={onClose}
    >
      <form onSubmit={submit} className={ui.form}>
        {suspending ? (
          <>
            <p className={ui.muted} style={{ margin: 0 }}>
              Access ends on their next request. Everything they created stays,
              with their name on it. Open work assigned to them will need a new
              owner.
            </p>
            <label className={ui.field}>
              <span>Reason</span>
              <TextArea
                limit={LIMITS.reference}
                rows={2}
                required
                value={reason}
                onChange={setReason}
              />
            </label>
          </>
        ) : (
          <p className={ui.muted} style={{ margin: 0 }}>
            They will be able to sign in again with their existing role.
          </p>
        )}
        <ErrorNotice error={mutation.error} fallback="Access could not be changed." />
        <div className={ui.actionsEnd}>
          <button type="button" className={ui.secondary} onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className={suspending ? ui.danger : ui.primary}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? 'Saving…' : suspending ? 'Suspend access' : 'Reinstate'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function InviteForm({ session }: { session: SessionContext }) {
  const mutation = useInviteMember(session.workspace.id)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('sales_rep')
  const [created, setCreated] = useState<InvitationCreated | null>(null)
  const [copied, setCopied] = useState(false)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    setCopied(false)
    mutation.mutate(
      { email, role },
      {
        onSuccess: (invitation) => {
          setCreated(invitation)
          setEmail('')
        },
      },
    )
  }

  async function copy(token: string) {
    try {
      await navigator.clipboard.writeText(token)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <section aria-labelledby="invite-heading" className={ui.card}>
      <h2 id="invite-heading" className={ui.sectionTitle}>
        Invite a colleague
      </h2>
      <form onSubmit={submit} className={ui.form} style={{ marginTop: 'var(--space-3)' }}>
        <div className={ui.formGrid}>
          <label className={ui.field}>
            <span>Email</span>
            <TextInput
              limit={LIMITS.email}
              type="email"
              required
              autoComplete="off"
              value={email}
              onChange={setEmail}
            />
          </label>
          <label className={ui.field}>
            <span>Role</span>
            <select value={role} onChange={(event) => setRole(event.target.value as Role)}>
              {INVITABLE.map((value) => (
                <option key={value} value={value}>
                  {ROLE_LABELS[value]}
                </option>
              ))}
            </select>
          </label>
        </div>
        <ErrorNotice error={mutation.error} fallback="The invitation could not be created." />
        <div className={ui.actions}>
          <button type="submit" className={ui.primary} disabled={mutation.isPending}>
            {mutation.isPending ? 'Creating…' : 'Create invitation'}
          </button>
        </div>
      </form>

      {created ? (
        <div className={ui.success} role="status" style={{ marginTop: 'var(--space-3)' }}>
          <p style={{ margin: '0 0 var(--space-2)' }}>
            Invitation created for <strong>{created.email}</strong> as{' '}
            {ROLE_LABELS[created.role]}. It expires {formatDateTime(created.expires_at)}.
          </p>
          <p style={{ margin: '0 0 var(--space-2)' }}>
            Send them this link by a private channel. It works once.{' '}
            <strong>It is shown once and cannot be recovered</strong> — only a hash
            of it is stored.
          </p>
          <code className={ui.code}>{joinLink(created.token)}</code>
          <div className={ui.actions} style={{ marginTop: 'var(--space-2)' }}>
            <button
              type="button"
              className={ui.secondary}
              onClick={() => void copy(joinLink(created.token))}
            >
              <Copy size={16} aria-hidden="true" />
              {copied ? 'Copied' : 'Copy link'}
            </button>
            <button type="button" className={ui.secondary} onClick={() => setCreated(null)}>
              Done
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}

function joinLink(token: string): string {
  return `${window.location.origin}/join?token=${encodeURIComponent(token)}`
}

function ExportPanel() {
  const mutation = useExportWorkspace()

  function run() {
    mutation.mutate(undefined, {
      onSuccess: ({ blob, filename }) => {
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = filename
        document.body.append(link)
        link.click()
        link.remove()
        URL.revokeObjectURL(url)
      },
    })
  }

  return (
    <section aria-labelledby="export-heading" className={ui.card}>
      <h2 id="export-heading" className={ui.sectionTitle}>
        Export the workspace
      </h2>
      <p className={ui.muted}>
        A ZIP of CSV files, one per record type, with stable identifiers so the
        relationships can be rebuilt elsewhere. The export is recorded in the
        audit log. Once downloaded, the file is outside this system's access
        controls — store it accordingly.
      </p>
      <ErrorNotice error={mutation.error} fallback="The export could not be produced." />
      {mutation.isSuccess ? (
        <p className={ui.success} role="status">
          Export downloaded.
        </p>
      ) : null}
      <div className={ui.actions} style={{ marginTop: 'var(--space-2)' }}>
        <button
          type="button"
          className={ui.primary}
          onClick={run}
          disabled={mutation.isPending}
        >
          <Download size={16} aria-hidden="true" />
          {mutation.isPending ? 'Preparing…' : 'Download export'}
        </button>
      </div>
    </section>
  )
}
