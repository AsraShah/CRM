import { Settings } from 'lucide-react'
import { Link, useOutletContext } from 'react-router'

import styles from './TeamPage.module.css'
import { Avatar, PageHeader, Stat } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { AccountabilityPage } from '@/features/accountability/AccountabilityPage'
import { ROLE_LABELS } from '@/features/settings/SettingsPage'
import { useMembers } from '@/lib/queries'
import type { SessionContext } from '@/lib/types'

/**
 * Team (SVX-PRD-001 section 1.3).
 *
 * Who is on the team and what they do, and the accountability queue: an
 * employee sees what was recorded about their own work and can respond; a
 * manager sees the queue. Inviting, suspending and other administration stay
 * in Settings.
 */
export function TeamPage() {
  const session = useOutletContext<SessionContext>()
  const { data, isLoading } = useMembers(session.workspace.id)
  const active = (data?.results ?? []).filter((m) => m.status === 'active')
  const sales = active.filter((m) => m.role === 'sales_rep' || m.role === 'sales_manager').length
  const delivery = active.filter(
    (m) => m.role === 'delivery_employee' || m.role === 'delivery_manager',
  ).length

  return (
    <div className={ui.page}>
      <PageHeader
        eyebrow="Insight"
        title="Team"
        subtitle={`Who works in ${session.workspace.name}, and anything that needs a conversation.`}
        actions={
          session.permissions.includes('membership.manage') ||
          session.permissions.includes('invitation.manage') ? (
            <Link to="/settings" className={ui.secondary}>
              <Settings size={16} aria-hidden="true" />
              Manage the team
            </Link>
          ) : null
        }
      />

      <div className={ui.stats}>
        <Stat feature label="People" value={active.length} meta="Active members" />
        <Stat label="Sales" value={sales} meta="Representatives and managers" />
        <Stat label="Delivery" value={delivery} meta="Employees and managers" />
      </div>

      <section aria-labelledby="people-heading" className={ui.stack}>
        <h2 id="people-heading" className={ui.sectionTitle}>
          People
        </h2>
        {isLoading ? (
          <p role="status" className={ui.muted}>
            Loading the team…
          </p>
        ) : (
          <ul className={styles.people}>
            {active.map((member) => (
              <li key={member.id} className={styles.person}>
                <Avatar name={member.full_name || member.email} large />
                <div className={styles.personText}>
                  <p className={styles.name}>
                    {member.full_name || member.email}
                    {member.user === session.user_id ? (
                      <span className={ui.pillAccent}>You</span>
                    ) : null}
                  </p>
                  {member.full_name ? <p className={styles.email}>{member.email}</p> : null}
                  <p className={ui.meta}>
                    <span className={ui.chip}>{ROLE_LABELS[member.role]}</span>
                    {member.team_name ? <span>{member.team_name}</span> : null}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <AccountabilityPage embedded />
    </div>
  )
}
