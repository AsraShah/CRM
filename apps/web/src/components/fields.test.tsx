import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { TextArea, TextInput } from './fields'
import { LIMITS, validators } from '@/lib/limits'

function Area({ initial = '', required = false }: { initial?: string; required?: boolean }) {
  const [value, setValue] = useState(initial)
  return (
    <label>
      <span>Outcome</span>
      <TextArea limit={20} required={required} value={value} onChange={setValue} />
    </label>
  )
}

function Phone({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial)
  return (
    <label>
      <span>Phone</span>
      <TextInput
        limit={LIMITS.phone}
        validate={validators.phone}
        type="tel"
        value={value}
        onChange={setValue}
      />
    </label>
  )
}

describe('TextArea', () => {
  it('stops typing at the limit the API enforces', () => {
    render(<Area />)
    expect(screen.getByRole('textbox', { name: 'Outcome' })).toHaveAttribute('maxLength', '20')
  })

  it('shows a live count without adding it to the field name', () => {
    const { container } = render(<Area initial="hello" />)
    expect(container.querySelector('[data-text="5 / 20"]')).not.toBeNull()
    // The counter is aria-hidden, so the accessible name stays "Outcome".
    expect(screen.getByRole('textbox', { name: 'Outcome' })).toBeInTheDocument()
    // And the label's own text is untouched, so label-text lookups still match exactly.
    expect(screen.getByLabelText('Outcome', { exact: true })).toBeInTheDocument()
  })

  it('refuses required text that is only spaces', () => {
    render(<Area required />)
    const field = screen.getByRole('textbox', { name: 'Outcome' }) as HTMLTextAreaElement
    fireEvent.change(field, { target: { value: '    ' } })
    expect(field.checkValidity()).toBe(false)
    expect(field.validationMessage).toMatch(/not only spaces/)

    fireEvent.change(field, { target: { value: 'Called back' } })
    expect(field.checkValidity()).toBe(true)
  })
})

describe('TextInput with a validator', () => {
  it('accepts an ordinary phone number', () => {
    render(<Phone initial="+92 300 1234567" />)
    expect((screen.getByRole('textbox', { name: 'Phone' }) as HTMLInputElement).checkValidity()).toBe(true)
  })

  it('refuses text in a phone field and says what is expected', () => {
    render(<Phone />)
    const field = screen.getByRole('textbox', { name: 'Phone' }) as HTMLInputElement
    fireEvent.change(field, { target: { value: 'call me' } })
    expect(field.checkValidity()).toBe(false)
    fireEvent.blur(field)
    expect(screen.getByText(/Use digits, spaces/)).toBeInTheDocument()
    expect(field).toHaveAttribute('aria-invalid', 'true')
  })

  it('leaves an empty optional field valid', () => {
    render(<Phone />)
    expect((screen.getByRole('textbox', { name: 'Phone' }) as HTMLInputElement).checkValidity()).toBe(true)
  })
})

describe('validators', () => {
  it('checks currency and country codes', () => {
    expect(validators.currency('usd')).toBeNull()
    expect(validators.currency('US1')).toMatch(/three-letter/)
    expect(validators.country('pk')).toBeNull()
    expect(validators.country('PAK')).toMatch(/two-letter/)
  })

  it('checks time zones against the browser list', () => {
    expect(validators.timeZone('Asia/Karachi')).toBeNull()
    expect(validators.timeZone('Mars/Olympus')).toMatch(/time zone/)
  })
})
