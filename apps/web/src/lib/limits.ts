/**
 * Input limits, mirrored from the API (apps/api/modules/common/limits.py).
 *
 * The server is the authority and refuses anything longer; these exist so
 * the interface stops the typing at the same point and shows the count,
 * rather than letting someone write three paragraphs and then fail.
 * Change both files together.
 */
export const LIMITS = {
  currency: 3,
  country: 2,
  phone: 40,
  jobTitle: 150,
  name: 200,
  evidenceReference: 200,
  email: 254,
  title: 300,
  reference: 500,
  shortReason: 1000,
  reason: 2000,
  longText: 5000,
  comment: 10000,
  password: 256,
  timeZone: 64,
} as const

/** Passwords: Django's validators require at least this many characters. */
export const PASSWORD_MIN = 12

/** Largest money amount the interface accepts (the API allows 16 integer digits). */
export const MONEY_MAX = 1_000_000_000_000

/** Same rule as the API: digits and phone punctuation, optional extension. */
const PHONE_PATTERN = /^\+?[0-9()\-.\s/]{3,}(?:\s*(?:x|ext\.?)\s*[0-9]{1,6})?$/i

/**
 * Validators return a message when the value is wrong, or null. An empty
 * value is left to `required`, so optional fields stay optional.
 */
export type Validator = (value: string) => string | null

export const validators = {
  phone: ((value) =>
    !value.trim() || PHONE_PATTERN.test(value.trim())
      ? null
      : 'Use digits, spaces, +, -, ( ) and an optional extension, for example +92 300 1234567.') as Validator,

  currency: ((value) =>
    !value || /^[A-Za-z]{3}$/.test(value)
      ? null
      : 'Use a three-letter currency code, for example USD.') as Validator,

  country: ((value) =>
    !value || /^[A-Za-z]{2}$/.test(value)
      ? null
      : 'Use a two-letter country code, for example PK.') as Validator,

  timeZone: ((value) => {
    if (!value.trim()) return null
    try {
      new Intl.DateTimeFormat(undefined, { timeZone: value.trim() })
      return null
    } catch {
      return 'Use a time zone name such as Asia/Karachi or Europe/London.'
    }
  }) as Validator,
}

/** "1,234 / 2,000": the counter text shown under long fields. */
export function formatCount(length: number, limit: number): string {
  const format = new Intl.NumberFormat()
  return `${format.format(length)} / ${format.format(limit)}`
}
