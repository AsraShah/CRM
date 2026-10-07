import type { ReactNode } from 'react'

import ui from './ui.module.css'

/**
 * Small shared building blocks, so every page has the same header, the same
 * people rendering, the same empty state and the same progress bar.
 */

export function PageHeader({
  title,
  subtitle,
  eyebrow,
  actions,
  children,
}: {
  title: string
  subtitle?: ReactNode
  eyebrow?: string
  /** Primary actions, right-aligned. */
  actions?: ReactNode
  /** Rendered under the title row: tabs, filters. */
  children?: ReactNode
}) {
  return (
    <header style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div className={ui.header}>
        <div style={{ minWidth: 0 }}>
          {eyebrow ? <p className={ui.eyebrow}>{eyebrow}</p> : null}
          <h1>{title}</h1>
          {subtitle ? <p className={ui.subtitle}>{subtitle}</p> : null}
        </div>
        {actions ? <div className={ui.toolbar}>{actions}</div> : null}
      </div>
      {children}
    </header>
  )
}

/** A stable colour per person, so the same colleague always looks the same. */
const AVATAR_COLOURS = [
  '#4338ca',
  '#0e7490',
  '#047857',
  '#b45309',
  '#be123c',
  '#7c3aed',
  '#0369a1',
  '#4d7c0f',
]

function colourFor(seed: string): string {
  let hash = 0
  for (const char of seed) hash = (hash * 31 + char.charCodeAt(0)) | 0
  return AVATAR_COLOURS[Math.abs(hash) % AVATAR_COLOURS.length]!
}

export function initials(name: string): string {
  const clean = name.replace(/@.*$/, '').replace(/[._-]+/g, ' ').trim()
  const parts = clean.split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase()
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase()
}

/** Decorative: the name beside it is the accessible text. */
export function Avatar({ name, large }: { name: string; large?: boolean }) {
  return (
    <span
      className={large ? `${ui.avatar} ${ui.avatarLarge}` : ui.avatar}
      style={{ background: colourFor(name) }}
      aria-hidden="true"
    >
      {initials(name)}
    </span>
  )
}

/** Avatar plus name and an optional second line. */
export function Person({ name, detail }: { name: string; detail?: ReactNode }) {
  return (
    <span className={ui.person}>
      <Avatar name={name} />
      <span className={ui.personText}>
        <span>{name}</span>
        {detail ? <span className={ui.hint}>{detail}</span> : null}
      </span>
    </span>
  )
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className={ui.emptyState}>
      {icon ? <span className={ui.emptyIcon}>{icon}</span> : null}
      <p className={ui.emptyTitle}>{title}</p>
      {children ? <p style={{ margin: 0, maxWidth: '28rem' }}>{children}</p> : null}
      {action ? <div style={{ marginTop: 'var(--space-3)' }}>{action}</div> : null}
    </div>
  )
}

export function Progress({ value, total, label }: { value: number; total: number; label: string }) {
  const percent = total > 0 ? Math.round((value / total) * 100) : 0
  return (
    <div
      className={ui.progress}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={value}
    >
      <div className={ui.progressBar} style={{ width: `${percent}%` }} />
    </div>
  )
}

export function Stat({
  label,
  value,
  meta,
  feature,
}: {
  label: string
  value: ReactNode
  meta?: ReactNode
  /** The solid-blue cell: the one figure the page leads with. */
  feature?: boolean
}) {
  return (
    <div className={feature ? `${ui.stat} ${ui.statFeature}` : ui.stat}>
      <span className={ui.statLabel}>{label}</span>
      <span className={ui.statValue}>{value}</span>
      {meta ? <span className={ui.statMeta}>{meta}</span> : null}
    </div>
  )
}
