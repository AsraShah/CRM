import styles from './ui.module.css'
import { ApiError } from '@/lib/api'

interface Props {
  error: unknown
  /** What failed, for a non-API error: "The ticket could not be saved." */
  fallback: string
}

/**
 * The one way a failed write is shown.
 *
 * A version conflict gets its own wording because the right response differs:
 * reload and decide again, rather than retry. Field errors are listed so the
 * user is told which field to fix, not only that something was wrong.
 */
export function ErrorNotice({ error, fallback }: Props) {
  if (!error) return null

  if (error instanceof ApiError && error.isVersionConflict) {
    return (
      <p role="alert" className={styles.warning}>
        Somebody changed this record while you were working on it. Reload the
        page to see the current version, then decide again. Nothing has been
        overwritten.
      </p>
    )
  }

  if (error instanceof ApiError) {
    const fields = Object.entries(error.fieldErrors).filter(
      ([, messages]) => messages.length > 0,
    )
    return (
      <div role="alert" className={styles.error}>
        <p style={{ margin: 0 }}>{error.message}</p>
        {fields.length > 0 ? (
          <ul style={{ margin: 'var(--space-1) 0 0', paddingLeft: 'var(--space-5)' }}>
            {fields.map(([field, messages]) => (
              <li key={field}>
                {humanise(field)}: {messages.join(' ')}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    )
  }

  return (
    <p role="alert" className={styles.error}>
      {error instanceof Error && error.message ? error.message : fallback}
    </p>
  )
}

function humanise(field: string): string {
  const text = field.replace(/_id$/, '').replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}
