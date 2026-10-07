import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { TaskRow } from './TaskRow'
import type { Task } from '@/lib/types'

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-1',
    title: 'Call the prospect',
    description: '',
    kind: 'follow_up',
    status: 'open',
    owner: 'user-1',
    owner_email: 'rep@scalevexo.test',
    origin: 'user',
    due_at: '2026-09-29T10:00:00Z',
    original_due_at: '2026-09-29T10:00:00Z',
    reschedule_count: 0,
    is_overdue: false,
    contact: 'contact-1',
    contact_name: 'Acme Ltd',
    opportunity: null,
    opportunity_service: null,
    lead: null,
    project: null,
    milestone: null,
    completed_at: null,
    outcome: '',
    stop_reason: '',
    blocked_reason: '',
    version: 1,
    created_at: '2026-09-28T09:00:00Z',
    ...overrides,
  }
}

describe('TaskRow', () => {
  it('shows the customer and the purpose together', () => {
    render(<TaskRow task={makeTask()} onComplete={vi.fn()} />)
    expect(screen.getByText('Call the prospect')).toBeInTheDocument()
    expect(screen.getByText('Acme Ltd')).toBeInTheDocument()
  })

  it('states that a task is overdue in text, not only in colour', () => {
    // Colour alone fails colour-blind users and screen readers entirely.
    render(<TaskRow task={makeTask({ is_overdue: true })} onComplete={vi.fn()} />)
    expect(screen.getByText(/overdue/i)).toBeInTheDocument()
  })

  it('keeps the original deadline visible after a reschedule', () => {
    // CRM05: a changed deadline must not erase the original commitment.
    render(
      <TaskRow
        task={makeTask({
          reschedule_count: 2,
          due_at: '2026-10-05T10:00:00Z',
          original_due_at: '2026-09-29T10:00:00Z',
        })}
        onComplete={vi.fn()}
      />,
    )
    expect(screen.getByText(/moved 2 times/i)).toBeInTheDocument()
    expect(screen.getByText(/originally due/i)).toBeInTheDocument()
  })

  it('marks an automatically created task as such', () => {
    // CRM10: a rule-generated task is not a commitment the person made.
    render(<TaskRow task={makeTask({ origin: 'rule' })} onComplete={vi.fn()} />)
    expect(screen.getByText(/created automatically/i)).toBeInTheDocument()
  })

  it('gives the action button an accessible name that names the task', () => {
    render(<TaskRow task={makeTask()} onComplete={vi.fn()} />)
    expect(
      screen.getByRole('button', { name: /record outcome for call the prospect/i }),
    ).toBeInTheDocument()
  })
})
