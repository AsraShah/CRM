import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { TicketActions } from './TicketActions'
import type { SessionContext, Ticket, TicketState } from '@/lib/types'

function makeTicket(state: TicketState): Ticket {
  return {
    id: 't-1',
    title: 'Contact form broken',
    description: '',
    origin: 'client_request',
    priority: 'normal',
    state,
    is_open: state !== 'closed',
    client: null,
    client_name: null,
    project: null,
    milestone: null,
    owner: null,
    owner_email: null,
    raised_by: null,
    waiting_reason: '',
    waiting_next_owner: null,
    waiting_review_at: null,
    waiting_overdue: false,
    next_action: '',
    resolution_note: '',
    closure_test_result: '',
    resolved_at: null,
    closed_at: null,
    reopen_count: 0,
    version: 1,
    created_at: '2026-10-01T09:00:00Z',
  }
}

const session: SessionContext = {
  user_id: 'u-1',
  email: 'someone@scalevexo.test',
  full_name: '',
  workspace: {
    id: 'w-1',
    name: 'ScaleVexo',
    slug: 'scalevexo',
    time_zone: 'UTC',
    calendar_version: 1,
    default_currency: 'USD',
  },
  membership_id: 'mem-1',
  role: 'delivery_employee',
  team_id: null,
  mfa_enrolled: false,
  permissions: ['ticket.manage'],
}

function buttonNames(): string[] {
  return screen.queryAllByRole('button').map((b) => b.textContent ?? '')
}

describe('TicketActions', () => {
  it('cannot resolve a ticket nobody has started', () => {
    render(<TicketActions ticket={makeTicket('new')} session={session} />)
    expect(buttonNames()).toEqual(['Triage', 'Close'])
  })

  it('keeps the two waiting states distinct', () => {
    render(<TicketActions ticket={makeTicket('in_progress')} session={session} />)
    expect(buttonNames()).toEqual(['Wait on the customer', 'Wait on us', 'Resolve'])
  })

  it('reopens a resolved ticket into triage', () => {
    render(<TicketActions ticket={makeTicket('resolved')} session={session} />)
    expect(buttonNames()).toEqual(['Close', 'Reopen'])
  })

  it('offers nothing without ticket.manage', () => {
    render(
      <TicketActions
        ticket={makeTicket('in_progress')}
        session={{ ...session, permissions: [] }}
      />,
    )
    expect(buttonNames()).toEqual([])
  })
})
