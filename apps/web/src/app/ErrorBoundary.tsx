import { isRouteErrorResponse, useRouteError } from 'react-router'

/**
 * Route-level error boundary.
 *
 * Shows what the user can do next. It never renders the underlying exception:
 * an error message can carry record identifiers or request detail that does not
 * belong on screen (section 6.2).
 */
export function ErrorBoundary() {
  const error = useRouteError()

  if (isRouteErrorResponse(error) && error.status === 404) {
    return (
      <div role="alert">
        <h1>That page does not exist</h1>
        <p>Check the address, or return to Today.</p>
        <a href="/today">Go to Today</a>
      </div>
    )
  }

  return (
    <div role="alert">
      <h1>Something went wrong</h1>
      <p>
        The page could not be displayed. Reload and try again. If this keeps
        happening, tell your administrator what you were doing.
      </p>
      <button type="button" onClick={() => window.location.reload()}>
        Reload the page
      </button>
    </div>
  )
}
