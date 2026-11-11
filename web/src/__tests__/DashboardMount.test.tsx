import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// Task #46: the dashboard's loading behaviour, held before its two lint warnings are fixed. The page loads the
// signed-in user ONCE when it mounts (not again after every state change), and for a publisher it loads the
// owner-scoped package list (/packages/mine) once.

vi.mock('@/lib/api', () => ({
  fetchWithAuth: vi.fn(),
}))

import { fetchWithAuth } from '@/lib/api'
import DashboardPage from '@/app/dashboard/page'

const mockFetchWithAuth = fetchWithAuth as unknown as ReturnType<typeof vi.fn>

function res(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

const USER = {
  id: 'u-1',
  email: 'owner@example.invalid',
  username: 'owner',
  email_verified: true,
  two_factor_enabled: false,
  publisher: { slug: 'owner-pub', display_name: 'Owner Pub' },
}

function wire(user: unknown) {
  mockFetchWithAuth.mockImplementation((endpoint: string) => {
    if (endpoint === '/auth/me') return Promise.resolve(res(200, user))
    if (endpoint === '/packages/mine') return Promise.resolve(res(200, { packages: [] }))
    return Promise.resolve(res(200, {}))
  })
}

function calls(endpoint: string) {
  return mockFetchWithAuth.mock.calls.filter((c) => c[0] === endpoint).length
}

describe('Dashboard — loading on mount (task #46)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => ({}) })))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('loads the signed-in user exactly once, even after the page has re-rendered with the data', async () => {
    wire(USER)
    render(<DashboardPage />)
    await waitFor(() => expect(screen.getAllByText(/owner@example\.invalid/).length).toBeGreaterThan(0))
    // let any effect that would re-run after the state updates do so
    await new Promise((r) => setTimeout(r, 200))
    expect(calls('/auth/me')).toBe(1)
  })

  it("loads a publisher's own packages once, from the owner-scoped list", async () => {
    wire(USER)
    render(<DashboardPage />)
    await waitFor(() => expect(calls('/packages/mine')).toBe(1))
    await new Promise((r) => setTimeout(r, 200))
    expect(calls('/packages/mine')).toBe(1)
  })

  it('loads no package list for a user without a publisher', async () => {
    wire({ ...USER, publisher: null })
    render(<DashboardPage />)
    await waitFor(() => expect(calls('/auth/me')).toBe(1))
    await new Promise((r) => setTimeout(r, 200))
    expect(calls('/packages/mine')).toBe(0)
  })
})
