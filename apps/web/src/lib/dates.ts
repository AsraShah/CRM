/**
 * Date helpers for form fields.
 *
 * `<input type="datetime-local">` works in the browser's local zone with no
 * offset. The server stores UTC instants, so every value is converted on the
 * way out and nothing is sent as a bare local string the server would have to
 * guess about.
 */

/** A local "YYYY-MM-DDTHH:mm" value, a number of days from now at a set hour. */
export function localDateTimeFromNow(days: number, hour = 10): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  date.setHours(hour, 0, 0, 0)
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

/** Convert a datetime-local value to an ISO instant, or '' when empty. */
export function localToIso(value: string): string {
  return value ? new Date(value).toISOString() : ''
}

/** Today as "YYYY-MM-DD" in the browser's zone, for date inputs. */
export function todayLocalDate(): string {
  return localDateTimeFromNow(0).slice(0, 10)
}

export function formatDate(value: string | null): string {
  if (!value) return '—'
  // A bare date has no zone; parse it as local midnight, not UTC midnight,
  // or it shows as the previous day west of Greenwich.
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? new Date(`${value}T00:00:00`)
    : new Date(value)
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

export function formatDateTime(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}
