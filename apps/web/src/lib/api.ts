/**
 * The single API client (SVX-TECH-001 section 2.1).
 *
 * Everything goes through here so that CSRF, error shape and the 401/403
 * distinction are handled in one place rather than remembered at each call
 * site.
 */

const API_ROOT = '/api/v1'

/** The error envelope every endpoint returns (section 6.2). */
export interface ApiErrorBody {
  code: string
  message: string
  field_errors: Record<string, string[]>
  request_id: string
  /** Present on a 409 version conflict. */
  expected_version?: number
  current_version?: number
}

export class ApiError extends Error {
  readonly status: number
  readonly body: ApiErrorBody

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }

  /** The session is gone: the user must sign in again. */
  get isUnauthenticated(): boolean {
    return this.status === 401
  }

  /** Signed in, but this action is refused. Showing a login page would be wrong. */
  get isForbidden(): boolean {
    return this.status === 403
  }

  /**
   * Somebody else changed the record first. The caller reloads and lets the
   * user reconcile; it never retries silently.
   */
  get isVersionConflict(): boolean {
    return this.status === 409 && this.body.code === 'version_conflict'
  }

  get fieldErrors(): Record<string, string[]> {
    return this.body.field_errors ?? {}
  }
}

/** Read the CSRF cookie Django sets. It is deliberately not HttpOnly. */
function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)
  return match?.[1] ? decodeURIComponent(match[1]) : ''
}

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export interface RequestOptions {
  method?: string
  body?: unknown
  /** Sent on retryable writes so a repeat cannot duplicate the action. */
  idempotencyKey?: string
  signal?: AbortSignal
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }

  if (UNSAFE_METHODS.has(method)) {
    headers['X-CSRFToken'] = csrfToken()
  }
  if (options.idempotencyKey) {
    headers['Idempotency-Key'] = options.idempotencyKey
  }

  let payload: BodyInit | undefined
  if (options.body instanceof FormData) {
    // Let the browser set the multipart boundary.
    payload = options.body
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(options.body)
  }

  const response = await fetch(`${API_ROOT}${path}`, {
    method,
    headers,
    // null rather than undefined: exactOptionalPropertyTypes distinguishes
    // "absent" from "explicitly undefined", and RequestInit accepts null.
    body: payload ?? null,
    // Session cookies are the credential; there is no token in storage.
    credentials: 'same-origin',
    signal: options.signal ?? null,
  })

  if (response.status === 204) {
    return undefined as T
  }

  const contentType = response.headers.get('content-type') ?? ''
  const isJson = contentType.includes('application/json')

  if (!response.ok) {
    const body: ApiErrorBody = isJson
      ? await response.json()
      : {
          code: 'unexpected_response',
          message: `The server returned ${response.status}.`,
          field_errors: {},
          request_id: response.headers.get('X-Request-ID') ?? '',
        }
    throw new ApiError(response.status, body)
  }

  return isJson ? ((await response.json()) as T) : (undefined as T)
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) =>
    request<T>(path, signal ? { signal } : {}),
  post: <T>(path: string, body?: unknown, idempotencyKey?: string) =>
    request<T>(path, {
      method: 'POST',
      ...(body !== undefined ? { body } : {}),
      ...(idempotencyKey ? { idempotencyKey } : {}),
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', ...(body !== undefined ? { body } : {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', ...(body !== undefined ? { body } : {}) }),
}

/**
 * POST and receive a file (the workspace export, CRM13).
 *
 * Kept separate from request() because the success body is binary. Errors still
 * arrive as the JSON envelope, so they surface as the same ApiError.
 */
export async function downloadFile(
  path: string,
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(`${API_ROOT}${path}`, {
    method: 'POST',
    headers: { 'X-CSRFToken': csrfToken() },
    credentials: 'same-origin',
  })

  if (!response.ok) {
    const isJson = (response.headers.get('content-type') ?? '').includes(
      'application/json',
    )
    const body: ApiErrorBody = isJson
      ? await response.json()
      : {
          code: 'unexpected_response',
          message: `The server returned ${response.status}.`,
          field_errors: {},
          request_id: response.headers.get('X-Request-ID') ?? '',
        }
    throw new ApiError(response.status, body)
  }

  const disposition = response.headers.get('content-disposition') ?? ''
  const match = disposition.match(/filename="([^"]+)"/)
  return { blob: await response.blob(), filename: match?.[1] ?? 'export.zip' }
}

/** A random key for a write the user may retry after a network failure. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID()
}
