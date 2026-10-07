import {
  BarChart3,
  Briefcase,
  CalendarCheck,
  FolderKanban,
  LifeBuoy,
  LogOut,
  Menu,
  Settings,
  ShieldAlert,
  Users,
  Workflow,
  X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'

import styles from './AppLayout.module.css'
import { Avatar } from '@/components/kit'
import { ApiError } from '@/lib/api'
import { useSession } from '@/lib/queries'

interface NavItem {
  to: string
  label: string
  icon: typeof CalendarCheck
  /** Shown when the actor holds any one of these. Null: everyone. */
  anyOf: readonly string[] | null
  /** Other paths that belong to this section, for highlighting. */
  alsoActiveOn?: readonly string[]
}

/**
 * Primary navigation (SVX-PRD-001 section 1.3): Today, Leads and Deals,
 * Clients, Projects, Tickets, Team and Reports, with administration in
 * Settings. Grouped by the job each section serves.
 *
 * Items are hidden when the actor lacks the capability, so nobody is offered an
 * action the server would refuse. That is a usability aid only — the server
 * re-checks every call regardless.
 */
const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: 'Work',
    items: [{ to: '/today', label: 'Today', icon: CalendarCheck, anyOf: null }],
  },
  {
    label: 'Sales',
    items: [
      // One section for the list and the pipeline (CRM03 view switch).
      {
        to: '/leads',
        label: 'Leads and Deals',
        icon: Workflow,
        anyOf: ['lead.view', 'opportunity.view'],
        alsoActiveOn: ['/pipeline', '/import'],
      },
    ],
  },
  {
    label: 'Delivery',
    items: [
      { to: '/clients', label: 'Clients', icon: Briefcase, anyOf: ['project.view'] },
      { to: '/projects', label: 'Projects', icon: FolderKanban, anyOf: ['project.view'] },
      { to: '/tickets', label: 'Tickets', icon: LifeBuoy, anyOf: ['ticket.view'] },
    ],
  },
  {
    label: 'Insight',
    items: [
      // Everyone: the roster, and the accountability queue (CRM10).
      { to: '/team', label: 'Team', icon: Users, anyOf: null },
      { to: '/reports', label: 'Reports', icon: BarChart3, anyOf: ['report.team'] },
    ],
  },
]

const SETTINGS: NavItem = {
  to: '/settings',
  label: 'Settings',
  icon: Settings,
  anyOf: [
    'membership.manage',
    'invitation.manage',
    'rule.manage',
    'workspace.configure',
    'workspace.export',
  ],
}

export function AppLayout() {
  const { data: session, isLoading, isError, error } = useSession()
  const { pathname } = useLocation()
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Close the phone drawer after navigating.
  useEffect(() => setDrawerOpen(false), [pathname])

  if (isLoading) {
    return (
      <div className={styles.centered} role="status" aria-live="polite">
        <img className={styles.brandMark} src="/brand/emblem.webp" alt="" width={28} height={28} />
        Loading your workspace…
      </div>
    )
  }

  if (isError || !session) {
    // A 401 means the session ended: a full navigation to sign in. A 403 is
    // different — the credentials are fine, so a login page would only loop.
    if (error instanceof ApiError && error.isUnauthenticated) {
      window.location.assign('/accounts/login/')
      return null
    }
    return (
      <div className={styles.centered} role="alert">
        <h1>Your workspace could not be loaded</h1>
        <p>Reload the page. If this continues, contact your administrator.</p>
      </div>
    )
  }

  const allowed = (item: NavItem) =>
    item.anyOf === null || item.anyOf.some((p) => session.permissions.includes(p))
  const isActive = (item: NavItem, active: boolean) =>
    active || Boolean(item.alsoActiveOn?.some((path) => pathname.startsWith(path)))
  const name = session.full_name || session.email
  const needsMfa = !session.mfa_enrolled && (session.role === 'owner' || session.role === 'admin')

  const link = (item: NavItem) => (
    <li key={item.to}>
      <NavLink
        to={item.to}
        className={({ isActive: active }) =>
          isActive(item, active) ? `${styles.navLink} ${styles.navLinkActive}` : styles.navLink
        }
      >
        {/* The icon is decorative; the text label is the accessible name. */}
        <item.icon size={18} aria-hidden="true" />
        <span>{item.label}</span>
      </NavLink>
    </li>
  )

  return (
    <div className={styles.shell}>
      <a className={styles.skipLink} href="#main">
        Skip to main content
      </a>

      {/* Phone top bar: brand and the menu button. Hidden on wide screens. */}
      <header className={styles.mobileBar}>
        <span className={styles.brand}>
          <img className={styles.brandMark} src="/brand/emblem.webp" alt="" width={28} height={28} />
          {session.workspace.name}
        </span>
        <button
          type="button"
          className={styles.menuButton}
          aria-expanded={drawerOpen}
          aria-controls="sidebar"
          onClick={() => setDrawerOpen((open) => !open)}
        >
          {drawerOpen ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
          <span className="visually-hidden">{drawerOpen ? 'Close menu' : 'Open menu'}</span>
        </button>
      </header>

      <aside
        id="sidebar"
        className={drawerOpen ? `${styles.sidebar} ${styles.sidebarOpen}` : styles.sidebar}
      >
        <div className={styles.sidebarBrand}>
          <img className={styles.brandMark} src="/brand/emblem.webp" alt="" width={28} height={28} />
          <span className={styles.brandText}>
            <span className={styles.brandName}>ScaleVexo</span>
            <span className={styles.workspace}>{session.workspace.name}</span>
          </span>
        </div>

        <nav className={styles.nav} aria-label="Primary">
          {NAV_GROUPS.map((group) => {
            const items = group.items.filter(allowed)
            if (items.length === 0) return null
            return (
              <div key={group.label} className={styles.navGroup}>
                <p className={styles.navGroupLabel}>{group.label}</p>
                <ul>{items.map(link)}</ul>
              </div>
            )
          })}
          {allowed(SETTINGS) ? (
            <div className={`${styles.navGroup} ${styles.navGroupEnd}`}>
              <ul>{link(SETTINGS)}</ul>
            </div>
          ) : null}
        </nav>

        <div className={styles.userCard}>
          {needsMfa ? (
            // Privileged capabilities stay locked until a second factor exists,
            // so the reason must be visible rather than looking like a bug.
            <a className={styles.mfaWarning} href="/accounts/2fa/totp/activate/">
              <ShieldAlert size={16} aria-hidden="true" />
              Set up two-factor authentication
            </a>
          ) : null}
          <div className={styles.user}>
            <Avatar name={name} />
            <span className={styles.userText}>
              <span className={styles.userName}>{name}</span>
              <span className={styles.userRole}>{formatRole(session.role)}</span>
            </span>
            <a className={styles.signOut} href="/accounts/logout/" title="Sign out">
              <LogOut size={16} aria-hidden="true" />
              <span className="visually-hidden">Sign out</span>
            </a>
          </div>
        </div>
      </aside>

      {drawerOpen ? (
        <div className={styles.scrim} aria-hidden="true" onClick={() => setDrawerOpen(false)} />
      ) : null}

      <main id="main" className={styles.main} tabIndex={-1}>
        <div className={styles.content}>
          <Outlet context={session} />
        </div>
      </main>
    </div>
  )
}

function formatRole(role: string): string {
  return role.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
