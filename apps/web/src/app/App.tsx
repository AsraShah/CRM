import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router'

import { AppLayout } from './AppLayout'
import { ErrorBoundary } from './ErrorBoundary'
import { ClientRecordPage } from '@/features/clients/ClientRecordPage'
import { ClientsPage } from '@/features/clients/ClientsPage'
import { ImportPage } from '@/features/import/ImportPage'
import { JoinPage } from '@/features/join/JoinPage'
import { LeadsPage } from '@/features/leads/LeadsPage'
import { PipelinePage } from '@/features/pipeline/PipelinePage'
import { ProjectsPage } from '@/features/projects/ProjectsPage'
import { ReportsPage } from '@/features/reports/ReportsPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { TeamPage } from '@/features/team/TeamPage'
import { TicketsPage } from '@/features/tickets/TicketsPage'
import { TodayPage } from '@/features/today/TodayPage'
import { ApiError } from '@/lib/api'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Authentication and authorisation failures are terminal: retrying a 403
      // three times just delays telling the user what happened.
      retry: (failureCount, error) => {
        if (error instanceof ApiError) {
          if (error.status >= 400 && error.status < 500) return false
        }
        return failureCount < 2
      },
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
    mutations: { retry: false },
  },
})

const router = createBrowserRouter([
  // Outside the app shell: the visitor has no session or membership yet.
  { path: '/join', element: <JoinPage />, errorElement: <ErrorBoundary /> },
  {
    path: '/',
    element: <AppLayout />,
    errorElement: <ErrorBoundary />,
    children: [
      { index: true, element: <Navigate to="/today" replace /> },
      { path: 'today', element: <TodayPage /> },
      { path: 'leads', element: <LeadsPage /> },
      { path: 'pipeline', element: <PipelinePage /> },
      { path: 'clients', element: <ClientsPage /> },
      { path: 'clients/:clientId', element: <ClientRecordPage /> },
      { path: 'projects', element: <ProjectsPage /> },
      { path: 'tickets', element: <TicketsPage /> },
      { path: 'team', element: <TeamPage /> },
      // The queue now lives under Team; keep old links working.
      { path: 'accountability', element: <Navigate to="/team" replace /> },
      { path: 'reports', element: <ReportsPage /> },
      { path: 'import', element: <ImportPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
])

export function App() {
  return (
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>
  )
}
