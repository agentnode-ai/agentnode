import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// The login page uses Suspense + useSearchParams, so we need to mock
// useSearchParams to return a real URLSearchParams object.
// The global mock in setup.ts handles this.

// We need to mock fetch since the form calls /api/v1/auth/login
const mockFetch = vi.fn()
global.fetch = mockFetch

// Import the page component (it exports LoginPage as default, wrapping LoginContent in Suspense)
import LoginPage from '@/app/auth/login/page'

describe('LoginForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the sign in heading', () => {
    render(<LoginPage />)
    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
  })

  it('renders email and password fields', () => {
    render(<LoginPage />)
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('••••••••')).toBeInTheDocument()
  })

  it('renders email input with correct type', () => {
    render(<LoginPage />)
    const emailInput = screen.getByPlaceholderText('you@example.com')
    expect(emailInput).toHaveAttribute('type', 'email')
  })

  it('renders password input with correct type', () => {
    render(<LoginPage />)
    const passwordInput = screen.getByPlaceholderText('••••••••')
    expect(passwordInput).toHaveAttribute('type', 'password')
  })

  it('renders the submit button with "Sign in" text', () => {
    render(<LoginPage />)
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('submit button is clickable', async () => {
    render(<LoginPage />)
    const button = screen.getByRole('button', { name: /sign in/i })
    expect(button).not.toBeDisabled()
  })

  it('both fields are marked as required', () => {
    render(<LoginPage />)
    const emailInput = screen.getByPlaceholderText('you@example.com')
    const passwordInput = screen.getByPlaceholderText('••••••••')
    expect(emailInput).toBeRequired()
    expect(passwordInput).toBeRequired()
  })

  it('allows typing in email field', async () => {
    const user = userEvent.setup()
    render(<LoginPage />)
    const emailInput = screen.getByPlaceholderText('you@example.com')

    await user.type(emailInput, 'test@example.com')
    expect(emailInput).toHaveValue('test@example.com')
  })

  it('allows typing in password field', async () => {
    const user = userEvent.setup()
    render(<LoginPage />)
    const passwordInput = screen.getByPlaceholderText('••••••••')

    await user.type(passwordInput, 'secret123')
    expect(passwordInput).toHaveValue('secret123')
  })

  it('shows error message when login fails', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ error: { message: 'Invalid credentials' } }),
    })

    render(<LoginPage />)

    await user.type(screen.getByPlaceholderText('you@example.com'), 'bad@example.com')
    await user.type(screen.getByPlaceholderText('••••••••'), 'wrong')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByText('Invalid credentials')).toBeInTheDocument()
    })
  })

  it('shows "Signing in..." while loading', async () => {
    const user = userEvent.setup()

    // Make fetch hang so we can observe the loading state
    let resolveLogin: (value: unknown) => void
    mockFetch.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveLogin = resolve
      })
    )

    render(<LoginPage />)

    await user.type(screen.getByPlaceholderText('you@example.com'), 'a@b.com')
    await user.type(screen.getByPlaceholderText('••••••••'), 'pass')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(screen.getByText('Signing in...')).toBeInTheDocument()

    // Resolve to clean up
    resolveLogin!({
      ok: true,
      json: async () => ({ token: 'abc' }),
    })
  })

  it('renders a link to the registration page', () => {
    render(<LoginPage />)
    const createLink = screen.getByRole('link', { name: /create one/i })
    expect(createLink).toHaveAttribute('href', '/auth/register')
  })

  it('renders a link to forgot password page', () => {
    render(<LoginPage />)
    const forgotLink = screen.getByRole('link', { name: /forgot your password/i })
    expect(forgotLink).toHaveAttribute('href', '/auth/forgot-password')
  })

  it('shows 2FA field when server requires it', async () => {
    const user = userEvent.setup()
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ error: { code: 'AUTH_2FA_REQUIRED', message: '2FA required' } }),
    })

    render(<LoginPage />)

    await user.type(screen.getByPlaceholderText('you@example.com'), 'user@example.com')
    await user.type(screen.getByPlaceholderText('••••••••'), 'correct')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('123456')).toBeInTheDocument()
    })
  })
})
