import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router'

import styles from './JoinPage.module.css'
import { TextInput } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import ui from '@/components/ui.module.css'
import { LIMITS, PASSWORD_MIN } from '@/lib/limits'
import { ApiError, api } from '@/lib/api'
import { formatDate } from '@/lib/dates'
import type { Member, Role } from '@/lib/types'

interface InvitationPreview {
  workspace_name: string
  email: string
  role: Role
  expires_at: string
  has_account: boolean
}

const ROLE_LABELS: Record<Role, string> = {
  owner: 'Owner',
  admin: 'Administrator',
  sales_manager: 'Sales manager',
  sales_rep: 'Sales representative',
  delivery_manager: 'Delivery manager',
  delivery_employee: 'Delivery employee',
  client: 'Client',
}

/**
 * Accept an invitation (CRM01).
 *
 * The only way into a workspace. A new person chooses a password here; someone
 * who already has an account signs in first, because the invitation link must
 * never be able to set the password of an account that already exists.
 */
export function JoinPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''

  const preview = useQuery({
    queryKey: ['invitation', token],
    queryFn: () =>
      api.get<InvitationPreview>(`/invitations/preview/?token=${encodeURIComponent(token)}`),
    enabled: Boolean(token),
    retry: false,
  })

  return (
    <main id="main" className={styles.screen}>
      <aside className={styles.hero}>
        <p className={styles.brand}>
          <img className={styles.brandMark} src="/brand/emblem.webp" alt="" width={32} height={32} />
          ScaleVexo
        </p>
        <div className={styles.heroBody}>
          <p className={styles.heroEyebrow}>Sales to delivery</p>
          <p className={styles.heroTitle}>
            One system for every lead, deal, project <span>and handover.</span>
          </p>
          <ol className={styles.heroList}>
            <li>Follow-ups that never slip</li>
            <li>Handovers delivery can accept</li>
            <li>Figures nobody has to rebuild</li>
          </ol>
        </div>
        <p className={styles.heroFoot}>ScaleVexo CRM · internal system</p>
      </aside>
      <div className={`${ui.card} ${styles.card}`}>
        <h1 className={styles.title}>Join your team</h1>
        {!token ? (
          <p className={ui.error} role="alert" style={{ marginTop: 'var(--space-3)' }}>
            This link is missing its invitation token. Ask the person who invited
            you to send it again.
          </p>
        ) : preview.isLoading ? (
          <p role="status">Checking your invitation…</p>
        ) : preview.isError || !preview.data ? (
          <div style={{ marginTop: 'var(--space-3)' }}>
            <ErrorNotice error={preview.error} fallback="This invitation could not be checked." />
          </div>
        ) : (
          <JoinForm token={token} invitation={preview.data} />
        )}
      </div>
    </main>
  )
}

function JoinForm({ token, invitation }: { token: string; invitation: InvitationPreview }) {
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const mismatch = confirm !== '' && confirm !== password

  const accept = useMutation({
    mutationFn: () =>
      api.post<Member>('/invitations/accept/', {
        token,
        full_name: fullName,
        password,
      }),
    // A full navigation, so the app starts fresh with the new session.
    onSuccess: () => window.location.assign('/today'),
  })

  const here = `/join?token=${encodeURIComponent(token)}`
  const signInFirst =
    accept.error instanceof ApiError && accept.error.isForbidden && invitation.has_account

  return (
    <>
      <p className={ui.muted}>
        You have been invited to <strong>{invitation.workspace_name}</strong> as{' '}
        {ROLE_LABELS[invitation.role]}, using <strong>{invitation.email}</strong>. The
        invitation expires {formatDate(invitation.expires_at)}.
      </p>

      {invitation.has_account ? (
        <div className={ui.form}>
          <p className={ui.notice}>
            An account already exists for this email. Sign in as{' '}
            {invitation.email}, and you will come back here to join.
          </p>
          {signInFirst ? (
            <ErrorNotice error={accept.error} fallback="Sign in first." />
          ) : (
            <ErrorNotice error={accept.error} fallback="The invitation could not be accepted." />
          )}
          <div className={ui.actions}>
            <a
              className={ui.secondary}
              href={`/accounts/login/?next=${encodeURIComponent(here)}`}
            >
              Sign in
            </a>
            <button
              type="button"
              className={ui.primary}
              disabled={accept.isPending}
              onClick={() => accept.mutate()}
            >
              {accept.isPending ? 'Joining…' : 'I am signed in — join'}
            </button>
          </div>
        </div>
      ) : (
        <form
          className={ui.form}
          onSubmit={(event) => {
            event.preventDefault()
            if (!mismatch) accept.mutate()
          }}
        >
          <label className={ui.field}>
            <span>Your name</span>
            <TextInput
              limit={LIMITS.name}
              type="text"
              autoComplete="name"
              required
              value={fullName}
              onChange={setFullName}
            />
          </label>
          <label className={ui.field}>
            <span>Choose a password</span>
            <span className={ui.hint}>At least 12 characters, not a common password.</span>
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={PASSWORD_MIN}
              maxLength={LIMITS.password}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <label className={ui.field}>
            <span>Confirm the password</span>
            <input
              type="password"
              autoComplete="new-password"
              required
              maxLength={LIMITS.password}
              value={confirm}
              aria-invalid={mismatch}
              onChange={(event) => setConfirm(event.target.value)}
            />
            {mismatch ? <span className={ui.hint}>The passwords do not match.</span> : null}
          </label>
          <ErrorNotice error={accept.error} fallback="The invitation could not be accepted." />
          <div className={ui.actions}>
            <button type="submit" className={ui.primary} disabled={accept.isPending || mismatch}>
              {accept.isPending ? 'Joining…' : 'Join and sign in'}
            </button>
          </div>
        </form>
      )}
    </>
  )
}
