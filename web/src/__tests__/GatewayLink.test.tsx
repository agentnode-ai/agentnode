import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// #37 "Connect a gateway": the website signs a link for the signed-in user and shows the one
// command the device runs. No real network, no real account and no real key: fetchWithAuth is
// mocked per test, and the grants below are placeholders the page only displays.
vi.mock('@/lib/api', () => ({
  fetchWithAuth: vi.fn(),
}))

import { fetchWithAuth } from '@/lib/api'
import GatewayLinkPage from '@/app/dashboard/gateway/page'

const mockFetch = fetchWithAuth as unknown as ReturnType<typeof vi.fn>

const ID = 'a'.repeat(32)
const ADDRESS = 'https://sandbox.example:8099'

function res(status: number, body: unknown) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body })
}

function answers(...queue: Array<[number, unknown]>) {
  mockFetch.mockImplementation((endpoint: string) => {
    if (endpoint !== '/auth/gateway-link') return res(404, {})
    const next = queue.shift()
    return next ? res(next[0], next[1]) : res(500, {})
  })
}

const inSeconds = (s: number) => Math.floor(Date.now() / 1000) + s

async function fillAndCreate(id = ID, address = ADDRESS) {
  const user = userEvent.setup()
  if (id) await user.type(screen.getByLabelText('Link id'), id)
  if (address) await user.type(screen.getByLabelText('Gateway address'), address)
  await user.click(screen.getByRole('button', { name: 'Create link' }))
  return user
}

describe('Connect a gateway', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it('signs a link for this gateway id and shows the one command to run', async () => {
    answers([200, { grant: 'GRANT-ONE', key_id: 'web-1', expires_at: inSeconds(300) }])
    render(<GatewayLinkPage />)
    await fillAndCreate()
    await waitFor(() =>
      expect(screen.getByTestId('link-command')).toHaveTextContent(
        `agentnode remote connect ${ADDRESS} --link GRANT-ONE`))
    const [endpoint, opts] = mockFetch.mock.calls[0]
    expect(endpoint).toBe('/auth/gateway-link')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual({ gateway_id: ID })
    expect(screen.getByText(/^It works once\./)).toBeInTheDocument()
  })

  it('refuses a malformed link id before asking the website', async () => {
    render(<GatewayLinkPage />)
    await fillAndCreate('not-an-id')
    expect(mockFetch).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/32/)
  })

  it('refuses an address that is not http(s) before asking the website', async () => {
    render(<GatewayLinkPage />)
    await fillAndCreate(ID, 'sandbox.example')
    expect(mockFetch).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/https:\/\//)
  })

  it('asks a signed-out visitor to sign in', async () => {
    answers([401, {}])
    render(<GatewayLinkPage />)
    await fillAndCreate()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/sign in/i))
    expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/auth/login')
    expect(screen.queryByTestId('link-command')).toBeNull()
  })

  it('says so when this website cannot sign links yet', async () => {
    answers([503, { error: { code: 'GATEWAY_LINK_NOT_CONFIGURED' } }])
    render(<GatewayLinkPage />)
    await fillAndCreate()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/not available/i))
    expect(screen.queryByTestId('link-command')).toBeNull()
  })

  it('passes on a refusal of the id by the website', async () => {
    answers([422, { error: { code: 'GATEWAY_LINK_BAD_GATEWAY' } }])
    render(<GatewayLinkPage />)
    await fillAndCreate()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/link id/i))
  })

  it('says to wait when asked too often', async () => {
    answers([429, {}])
    render(<GatewayLinkPage />)
    await fillAndCreate()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/too many/i))
  })

  it('hides the command once the link has expired and offers a new one', async () => {
    answers([200, { grant: 'GRANT-OLD', key_id: 'web-1', expires_at: inSeconds(-1) }],
            [200, { grant: 'GRANT-NEW', key_id: 'web-1', expires_at: inSeconds(300) }])
    render(<GatewayLinkPage />)
    const user = await fillAndCreate()
    await waitFor(() => expect(screen.getByText(/this link has expired/i)).toBeInTheDocument())
    expect(screen.queryByText(/GRANT-OLD/)).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Make a new link' }))
    await waitFor(() =>
      expect(screen.getByTestId('link-command')).toHaveTextContent('--link GRANT-NEW'))
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('replaces a link with a new one rather than showing two', async () => {
    answers([200, { grant: 'GRANT-ONE', key_id: 'web-1', expires_at: inSeconds(300) }],
            [200, { grant: 'GRANT-TWO', key_id: 'web-1', expires_at: inSeconds(300) }])
    render(<GatewayLinkPage />)
    const user = await fillAndCreate()
    await waitFor(() => expect(screen.getByTestId('link-command')).toHaveTextContent('GRANT-ONE'))
    await user.click(screen.getByRole('button', { name: 'Make a new link' }))
    await waitFor(() => expect(screen.getByTestId('link-command')).toHaveTextContent('GRANT-TWO'))
    expect(screen.queryByText(/GRANT-ONE/)).toBeNull()
  })

  it('keeps the grant nowhere but on the screen', async () => {
    answers([200, { grant: 'GRANT-SECRETISH', key_id: 'web-1', expires_at: inSeconds(300) }])
    render(<GatewayLinkPage />)
    await fillAndCreate()
    await waitFor(() => expect(screen.getByTestId('link-command')).toBeInTheDocument())
    const stored = JSON.stringify({ ...window.localStorage }) + JSON.stringify({ ...window.sessionStorage })
    expect(stored).not.toContain('GRANT-SECRETISH')
  })
})
