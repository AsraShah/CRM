import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ErrorNotice } from './ErrorNotice'
import { ApiError } from '@/lib/api'

describe('ErrorNotice', () => {
  it('tells the user to reload on a version conflict rather than retry', () => {
    const conflict = new ApiError(409, {
      code: 'version_conflict',
      message: 'Version conflict',
      field_errors: {},
      request_id: 'r-1',
    })
    render(<ErrorNotice error={conflict} fallback="Failed." />)
    expect(screen.getByRole('alert')).toHaveTextContent(/Reload the page/)
    expect(screen.getByRole('alert')).toHaveTextContent(/Nothing has been overwritten/)
  })

  it('names the fields that need fixing', () => {
    const invalid = new ApiError(400, {
      code: 'validation_failed',
      message: 'A waiting ticket needs a next owner and a review time.',
      field_errors: { waiting_next_owner_id: ['Required when waiting.'] },
      request_id: 'r-2',
    })
    render(<ErrorNotice error={invalid} fallback="Failed." />)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Waiting next owner: Required when waiting.',
    )
  })

  it('renders nothing without an error', () => {
    const { container } = render(<ErrorNotice error={null} fallback="Failed." />)
    expect(container).toBeEmptyDOMElement()
  })
})
