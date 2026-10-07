import { KanbanSquare, List } from 'lucide-react'
import { NavLink } from 'react-router'

import ui from './ui.module.css'

/**
 * Switch between the lead list and the pipeline (CRM03: "users shall be able
 * to switch between list and pipeline views"). Both live under one navigation
 * item, "Leads and Deals" (SVX-PRD-001 section 1.3).
 */
export function SalesViewSwitch() {
  return (
    <nav aria-label="Leads and deals view" className={ui.tabs}>
      <NavLink to="/leads" end className={({ isActive }) => (isActive ? ui.tabActive : ui.tab)}>
        <List size={15} aria-hidden="true" />
        Lead list
      </NavLink>
      <NavLink to="/pipeline" className={({ isActive }) => (isActive ? ui.tabActive : ui.tab)}>
        <KanbanSquare size={15} aria-hidden="true" />
        Deal pipeline
      </NavLink>
    </nav>
  )
}
