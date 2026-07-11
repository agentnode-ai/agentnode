import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Mock the API client so we control fetchWithAuth per test — no real network,
// and the page's initial /auth/me call is handled by the default impl.
vi.mock('@/lib/api', () => ({
  fetchWithAuth: vi.fn(),
}))

import { fetchWithAuth } from '@/lib/api'
import McpSubmitPage from '@/app/mcp/submit/page'

const mockFetchWithAuth = fetchWithAuth as unknown as ReturnType<typeof vi.fn>

interface FakeRes {
  ok?: boolean
  status: number
  json: () => Promise<unknown>
}

function res(status: number, body: unknown): FakeRes {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

/**
 * Wire fetchWithAuth: /auth/me always resolves to "not signed in" so the page
 * mounts cleanly; challenge/verify calls resolve to the per-test responses.
 * verify path is checked before challenge because it is a prefix superset.
 */
function wire({ challenge, verify }: { challenge?: FakeRes; verify?: FakeRes }) {
  mockFetchWithAuth.mockImplementation((endpoint: string) => {
    if (endpoint === '/auth/me') return Promise.resolve(res(401, {}))
    if (endpoint.startsWith('/mcp/ownership/challenge/verify')) {
      return Promise.resolve(verify ?? res(500, {}))
    }
    if (endpoint.startsWith('/mcp/ownership/challenge')) {
      return Promise.resolve(challenge ?? res(500, {}))
    }
    return Promise.resolve(res(404, {}))
  })
}

async function typePackage(name: string) {
  const user = userEvent.setup()
  const input = screen.getByPlaceholderText('e.g. my-mcp-server')
  await user.type(input, name)
  return user
}

describe('McpSubmit — publish-challenge ownership section', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the ownership section with registry + package + both buttons', async () => {
    wire({})
    render(<McpSubmitPage />)
    expect(
      screen.getByRole('heading', { name: /prove ownership via publish-challenge/i }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText(/registry/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/package name/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /issue challenge/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /verify ownership/i })).toBeInTheDocument()
  })

  it('issue success shows token, keyword, expiry and the "shown once" note', async () => {
    wire({
      challenge: res(200, {
        token: 'kJ3xR7-abc123',
        keyword: 'agentnode-ownership-kJ3xR7-abc123',
        expires_at: '2026-08-09T10:00:00+00:00',
      }),
    })
    render(<McpSubmitPage />)
    const user = await typePackage('my-mcp-server')
    await user.click(screen.getByRole('button', { name: /issue challenge/i }))

    await waitFor(() => {
      expect(screen.getByText('kJ3xR7-abc123')).toBeInTheDocument()
    })
    expect(screen.getByText('agentnode-ownership-kJ3xR7-abc123')).toBeInTheDocument()
    expect(screen.getByText('2026-08-09')).toBeInTheDocument()
    // "shown once" appears in both the heading and the hint paragraph.
    expect(screen.getAllByText(/shown once/i).length).toBeGreaterThan(0)
    expect(
      screen.getByText(/add this keyword to your package metadata, publish a new version, then verify/i),
    ).toBeInTheDocument()
  })

  it('verify success shows verified / strong ownership + version', async () => {
    wire({
      verify: res(200, {
        verified: true,
        status: 'verified',
        message: 'Ownership verified via a published version — strong evidence.',
        version: '1.1.0',
      }),
    })
    render(<McpSubmitPage />)
    const user = await typePackage('my-mcp-server')
    await user.click(screen.getByRole('button', { name: /verify ownership/i }))

    await waitFor(() => {
      expect(screen.getByText(/ownership verified — strong evidence/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/v1\.1\.0/)).toBeInTheDocument()
    expect(screen.getByText(/strong ownership evidence is recorded/i)).toBeInTheDocument()
  })

  it('verify pending tells the user to publish the keyword then verify again', async () => {
    wire({
      verify: res(200, {
        verified: false,
        status: 'pending',
        message: 'Token not found in the latest published version yet.',
        version: null,
      }),
    })
    render(<McpSubmitPage />)
    const user = await typePackage('my-mcp-server')
    await user.click(screen.getByRole('button', { name: /verify ownership/i }))

    await waitFor(() => {
      expect(screen.getByText(/not verified yet/i)).toBeInTheDocument()
    })
    expect(
      screen.getByText(/publish a version carrying the challenge keyword, then verify again/i),
    ).toBeInTheDocument()
  })

  it('401 shows a sign-in message', async () => {
    wire({ challenge: res(401, { error: { message: 'unauthorized' } }) })
    render(<McpSubmitPage />)
    const user = await typePackage('my-mcp-server')
    await user.click(screen.getByRole('button', { name: /issue challenge/i }))
    await waitFor(() => {
      expect(screen.getByText(/sign in to prove package ownership/i)).toBeInTheDocument()
    })
  })

  it('403 shows a publisher-account message', async () => {
    wire({ challenge: res(403, { error: { message: 'publisher required' } }) })
    render(<McpSubmitPage />)
    const user = await typePackage('my-mcp-server')
    await user.click(screen.getByRole('button', { name: /issue challenge/i }))
    await waitFor(() => {
      expect(screen.getByText(/proving ownership requires a publisher account/i)).toBeInTheDocument()
    })
  })

  it('404 no-challenge surfaces the server message on verify', async () => {
    wire({
      verify: res(404, {
        error: { code: 'MCP_NO_CHALLENGE', message: 'No publish-challenge to verify — issue one first.' },
      }),
    })
    render(<McpSubmitPage />)
    const user = await typePackage('my-mcp-server')
    await user.click(screen.getByRole('button', { name: /verify ownership/i }))
    await waitFor(() => {
      expect(screen.getByText(/issue one first/i)).toBeInTheDocument()
    })
  })

  it('404 package-not-found surfaces the server message on verify', async () => {
    wire({
      verify: res(404, {
        error: { code: 'MCP_PACKAGE_NOT_FOUND', message: "Package 'x' not found on npm." },
      }),
    })
    render(<McpSubmitPage />)
    const user = await typePackage('x')
    await user.click(screen.getByRole('button', { name: /verify ownership/i }))
    await waitFor(() => {
      expect(screen.getByText(/not found on npm/i)).toBeInTheDocument()
    })
  })

  it('400 expired surfaces the server message', async () => {
    wire({
      verify: res(400, {
        error: { code: 'MCP_CHALLENGE_EXPIRED', message: 'The challenge has expired — issue a new one.' },
      }),
    })
    render(<McpSubmitPage />)
    const user = await typePackage('x')
    await user.click(screen.getByRole('button', { name: /verify ownership/i }))
    await waitFor(() => {
      expect(screen.getByText(/expired/i)).toBeInTheDocument()
    })
  })

  it('503 registry-unavailable surfaces the server message', async () => {
    wire({
      verify: res(503, {
        error: { code: 'MCP_REGISTRY_UNAVAILABLE', message: 'The registry is temporarily unavailable.' },
      }),
    })
    render(<McpSubmitPage />)
    const user = await typePackage('x')
    await user.click(screen.getByRole('button', { name: /verify ownership/i }))
    await waitFor(() => {
      expect(screen.getByText(/temporarily unavailable/i)).toBeInTheDocument()
    })
  })

  it('honest copy: review-gated, and never claims auto-publish', () => {
    wire({})
    const { container } = render(<McpSubmitPage />)
    expect(
      screen.getByText(/ownership can be verified, but MCP listings still remain review-gated/i),
    ).toBeInTheDocument()
    const text = container.textContent || ''
    expect(text.toLowerCase()).not.toContain('will be auto-published')
    expect(text.toLowerCase()).not.toContain('goes live after verify')
    expect(text.toLowerCase()).not.toContain('publishes automatically')
  })
})
