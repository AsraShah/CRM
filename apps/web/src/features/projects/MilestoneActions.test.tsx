import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MilestoneActions } from './MilestoneActions'
import type { Milestone, SessionContext } from '@/lib/types'

function makeMilestone(overrides: Partial<Milestone> = {}): Milestone {
  return {
    id: 'm-1',
    project: 'p-1',
    name: 'Kick-off',
    description: '',
    sequence: 1,
    status: 'planned',
    owner: null,
    owner_email: null,
    due_on: null,
    is_overdue: false,
    submitted_at: null,
    submitted_by: null,
    evidence: '',
    accepted_at: null,
    accepted_by: null,
    acceptance_note: '',
    blocked_reason: '',
    blocked_next_owner: null,
    dependency_override_reason: '',
    cancelled_reason: '',
    cancellation_impact: '',
    unmet_dependencies: [],
    version: 1,
    ...overrides,
  }
}

function session(permissions: string[]): SessionContext {
  return {
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
    permissions,
  }
}

const EMPLOYEE = ['milestone.submit']
const MANAGER = ['milestone.submit', 'milestone.accept', 'project.manage']

function buttonNames(): string[] {
  return screen.queryAllByRole('button').map((b) => b.textContent ?? '')
}

describe('MilestoneActions', () => {
  it('lets an employee submit work but not accept it', () => {
    render(
      <MilestoneActions
        milestone={makeMilestone({ status: 'in_review' })}
        session={session(EMPLOYEE)}
      />,
    )
    // Submission and acceptance are separate acts by separate people (CRM08).
    expect(buttonNames()).not.toContain('Review and accept')
  })

  it('offers acceptance to a reviewer once work is submitted', () => {
    render(
      <MilestoneActions
        milestone={makeMilestone({ status: 'in_review' })}
        session={session(MANAGER)}
      />,
    )
    expect(buttonNames()).toContain('Review and accept')
  })

  it('offers submission only while work is in progress', () => {
    render(
      <MilestoneActions
        milestone={makeMilestone({ status: 'in_progress' })}
        session={session(EMPLOYEE)}
      />,
    )
    expect(buttonNames()).toContain('Submit for review')
  })

  it('lets blocked work be resumed', () => {
    render(
      <MilestoneActions
        milestone={makeMilestone({ status: 'blocked' })}
        session={session(EMPLOYEE)}
      />,
    )
    expect(buttonNames()).toContain('Resume work')
  })

  it('keeps blocking for managers, because it names a next owner', () => {
    const { unmount } = render(
      <MilestoneActions
        milestone={makeMilestone({ status: 'in_progress' })}
        session={session(EMPLOYEE)}
      />,
    )
    expect(buttonNames()).not.toContain('Mark blocked')
    unmount()

    render(
      <MilestoneActions
        milestone={makeMilestone({ status: 'in_progress' })}
        session={session(MANAGER)}
      />,
    )
    expect(buttonNames()).toContain('Mark blocked')
  })

  it('offers nothing on cancelled work', () => {
    render(
      <MilestoneActions
        milestone={makeMilestone({ status: 'cancelled' })}
        session={session(MANAGER)}
      />,
    )
    expect(buttonNames()).toEqual([])
  })
})
