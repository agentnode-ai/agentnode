import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import PackageCard from '@/components/PackageCard'

// Mock the child badge components to keep tests focused on PackageCard itself
vi.mock('@/components/TrustBadge', () => ({
  __esModule: true,
  default: ({ level }: { level: string }) => (
    <span data-testid="trust-badge">{level}</span>
  ),
}))

vi.mock('@/components/VerificationBadge', () => ({
  __esModule: true,
  default: ({ tier, status }: { tier?: string | null; status?: string | null }) => (
    <span data-testid="verification-badge">{tier ?? status ?? 'none'}</span>
  ),
}))

const baseProps = {
  slug: 'web-scraper',
  summary: 'Scrapes web pages and extracts content',
  trust_level: 'verified' as const,
  frameworks: ['langchain', 'crewai'],
}

describe('PackageCard', () => {
  it('renders the package name when provided', () => {
    render(<PackageCard {...baseProps} name="Web Scraper" />)
    expect(screen.getByText('Web Scraper')).toBeInTheDocument()
  })

  it('falls back to slug when name is not provided', () => {
    render(<PackageCard {...baseProps} />)
    expect(screen.getByText('web-scraper')).toBeInTheDocument()
  })

  it('renders the summary text', () => {
    render(<PackageCard {...baseProps} />)
    expect(screen.getByText('Scrapes web pages and extracts content')).toBeInTheDocument()
  })

  it('links to the correct package URL', () => {
    render(<PackageCard {...baseProps} />)
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', '/packages/web-scraper')
  })

  it('shows version number when provided', () => {
    render(<PackageCard {...baseProps} version="1.2.3" />)
    expect(screen.getByText('v1.2.3')).toBeInTheDocument()
  })

  it('does not show version when not provided', () => {
    render(<PackageCard {...baseProps} />)
    expect(screen.queryByText(/^v\d/)).not.toBeInTheDocument()
  })

  it('renders framework tags', () => {
    render(<PackageCard {...baseProps} />)
    expect(screen.getByText('langchain')).toBeInTheDocument()
    expect(screen.getByText('crewai')).toBeInTheDocument()
  })

  it('shows publisher name when provided', () => {
    render(<PackageCard {...baseProps} publisher_name="acme-corp" />)
    expect(screen.getByText('by acme-corp')).toBeInTheDocument()
  })

  it('does not show publisher name when not provided', () => {
    render(<PackageCard {...baseProps} />)
    expect(screen.queryByText(/^by /)).not.toBeInTheDocument()
  })

  it('shows download count when provided', () => {
    render(<PackageCard {...baseProps} download_count={1500} />)
    expect(screen.getByText('1.5k downloads')).toBeInTheDocument()
  })

  it('formats large download counts with M suffix', () => {
    render(<PackageCard {...baseProps} download_count={2_500_000} />)
    expect(screen.getByText('2.5M downloads')).toBeInTheDocument()
  })

  it('shows raw count for small numbers', () => {
    render(<PackageCard {...baseProps} download_count={42} />)
    expect(screen.getByText('42 downloads')).toBeInTheDocument()
  })

  it('does not show download count when not provided', () => {
    render(<PackageCard {...baseProps} />)
    expect(screen.queryByText(/downloads/)).not.toBeInTheDocument()
  })

  it('shows package_type badge when not toolpack', () => {
    render(<PackageCard {...baseProps} package_type="upgrade" />)
    expect(screen.getByText('upgrade')).toBeInTheDocument()
  })

  it('hides package_type badge when it is toolpack', () => {
    render(<PackageCard {...baseProps} package_type="toolpack" />)
    // "toolpack" should not appear as a badge — the component skips it
    const allText = document.body.textContent || ''
    // The slug 'web-scraper' and summary will be present, but not "toolpack" as a badge
    expect(screen.queryByText('toolpack')).not.toBeInTheDocument()
  })

  it('renders trust and verification badges', () => {
    render(
      <PackageCard
        {...baseProps}
        verification_tier="gold"
        verification_score={95}
      />
    )
    expect(screen.getByTestId('trust-badge')).toHaveTextContent('verified')
    expect(screen.getByTestId('verification-badge')).toHaveTextContent('gold')
  })

  it('handles missing optional fields gracefully', () => {
    // Render with only required props — should not throw
    render(
      <PackageCard
        slug="minimal-tool"
        summary="A minimal tool"
        trust_level="unverified"
        frameworks={[]}
      />
    )
    expect(screen.getByText('minimal-tool')).toBeInTheDocument()
    expect(screen.getByText('A minimal tool')).toBeInTheDocument()
  })
})
