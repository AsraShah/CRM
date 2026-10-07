import { useMembers } from '@/lib/queries'
import type { Role } from '@/lib/types'

interface Props {
  workspaceId: string
  /** A user id, which is what every owner field on the server takes. */
  value: string
  onChange: (userId: string) => void
  required?: boolean
  /** Label for the empty choice. Omitted when a choice is required. */
  emptyLabel?: string
  /** Restrict the list, for example to delivery staff for a delivery owner. */
  roles?: readonly Role[]
  id?: string
}

/**
 * Pick a colleague.
 *
 * Suspended and invited members are left out: the server refuses to assign
 * work to them, so offering them would only produce an error.
 */
export function MemberSelect({
  workspaceId,
  value,
  onChange,
  required,
  emptyLabel,
  roles,
  id,
}: Props) {
  const { data, isLoading, isError } = useMembers(workspaceId)

  const members = (data?.results ?? []).filter(
    (member) =>
      member.status === 'active' &&
      member.role !== 'client' &&
      (!roles || roles.includes(member.role)),
  )

  return (
    <select
      id={id}
      value={value}
      required={required}
      disabled={isLoading}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">
        {isLoading
          ? 'Loading colleagues…'
          : isError
            ? 'Colleagues could not be loaded'
            : (emptyLabel ?? 'Choose a person')}
      </option>
      {members.map((member) => (
        <option key={member.id} value={member.user}>
          {member.full_name ? `${member.full_name} (${member.email})` : member.email}
        </option>
      ))}
    </select>
  )
}
