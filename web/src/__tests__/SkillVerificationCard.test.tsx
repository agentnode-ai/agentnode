import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import VerificationMainPanel from '@/app/packages/[slug]/VerificationMainPanel'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}))

vi.mock('@/lib/api', () => ({
  fetchWithAuth: vi.fn(async () => ({ ok: false, json: async () => ({}) })),
}))

function skillVerification(overrides: Record<string, unknown> = {}) {
  return {
    status: 'passed',
    score: 33,
    tier: 'unverified',
    confidence: 'low',
    verification_mode: 'prompt_only',
    steps: [
      { name: 'install', status: 'passed', duration_ms: 0 },
      { name: 'import', status: 'passed' },
    ],
    score_breakdown: {
      score: 33,
      tier: 'unverified',
      breakdown: {
        smoke: { points: 0, max: 25, reason: 'Not applicable: prompt-only skill runs no code' },
      },
      explanation: 'Prompt-only skill: files present and readable, content check passed. It runs no code.',
    },
    environment: { python_version: '3.12.3 (main)', system_capabilities: { ffmpeg: true } },
    last_verified_at: new Date().toISOString(),
    ...overrides,
  }
}

describe('Verification panel for a prompt-only skill', () => {
  it('shows the skill check, not the code score', () => {
    render(<VerificationMainPanel slug="demo" verification={skillVerification()} />)
    expect(screen.getByText('Skill check')).toBeInTheDocument()
    expect(screen.getByText('✔ Checked')).toBeInTheDocument()
    expect(screen.getByText(/No hidden characters, secrets or harmful instructions found/)).toBeInTheDocument()
    expect(screen.queryByText('33/100')).not.toBeInTheDocument()
    expect(screen.queryByText(/below the verification threshold/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Python 3.12.3/)).not.toBeInTheDocument()
    expect(screen.queryByText(/prompt_only mode/)).not.toBeInTheDocument()
  })

  it('shows a skill held for review as in review', () => {
    render(
      <VerificationMainPanel
        slug="demo"
        verification={skillVerification({
          score_breakdown: {
            breakdown: {},
            explanation:
              'Prompt-only skill: files present and readable; the content check flagged text for a human review. It runs no code.',
          },
        })}
      />,
    )
    expect(screen.getByText('○ In review')).toBeInTheDocument()
    expect(screen.getByText(/Flagged for a human review/)).toBeInTheDocument()
  })

  it('shows a failed content check with its reason', () => {
    render(
      <VerificationMainPanel
        slug="demo"
        verification={skillVerification({
          status: 'failed',
          steps: [
            { name: 'install', status: 'passed' },
            { name: 'import', status: 'failed' },
          ],
          error_summary: 'Skill content check found 1 high-risk finding(s): SKILL.md:2: instructs the agent to send something covertly',
        })}
      />,
    )
    expect(screen.getByText('✖ Failed')).toBeInTheDocument()
    expect(screen.getByText(/High-risk text found/)).toBeInTheDocument()
    expect(screen.getByText(/Reason: Skill content check found 1 high-risk/)).toBeInTheDocument()
  })

  it('leaves code packages on the scored panel', () => {
    render(
      <VerificationMainPanel
        slug="demo"
        verification={skillVerification({ verification_mode: 'real', tier: 'unverified' })}
      />,
    )
    expect(screen.getByText('Verification')).toBeInTheDocument()
    expect(screen.queryByText('Skill check')).not.toBeInTheDocument()
    expect(screen.getByText('33/100')).toBeInTheDocument()
  })
})
