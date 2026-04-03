import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

/**
 * The search page has inline pagination (not a standalone component).
 * To test pagination in isolation, we extract the rendering logic into
 * a small helper component that mirrors the search page's pagination
 * markup and behavior exactly.
 */

interface PaginationProps {
  page: number
  totalPages: number
  onPageChange: (page: number) => void
}

/**
 * Extracted pagination — mirrors the exact markup from search/page.tsx.
 * This lets us test the pagination UI without the full search page,
 * which requires fetch, useSearchParams, etc.
 */
function Pagination({ page, totalPages, onPageChange }: PaginationProps) {
  if (totalPages <= 1) return null

  const pages: (number | '...')[] = []
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pages.push(i)
  } else {
    pages.push(1)
    if (page > 3) pages.push('...')
    for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) {
      pages.push(i)
    }
    if (page < totalPages - 2) pages.push('...')
    pages.push(totalPages)
  }

  return (
    <nav className="mt-8 flex items-center justify-center gap-1" aria-label="Pagination">
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1}
      >
        Previous
      </button>

      {pages.map((p, i) =>
        p === '...' ? (
          <span key={`dots-${i}`} aria-hidden="true">...</span>
        ) : (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            aria-label={`Page ${p}`}
            aria-current={p === page ? 'page' : undefined}
          >
            {p}
          </button>
        )
      )}

      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page >= totalPages}
      >
        Next
      </button>
    </nav>
  )
}

describe('Pagination', () => {
  it('renders nothing when there is only one page', () => {
    const { container } = render(
      <Pagination page={1} totalPages={1} onPageChange={() => {}} />
    )
    expect(container.querySelector('nav')).toBeNull()
  })

  it('renders page buttons for multiple pages', () => {
    render(<Pagination page={1} totalPages={5} onPageChange={() => {}} />)
    expect(screen.getByLabelText('Page 1')).toBeInTheDocument()
    expect(screen.getByLabelText('Page 2')).toBeInTheDocument()
    expect(screen.getByLabelText('Page 3')).toBeInTheDocument()
    expect(screen.getByLabelText('Page 4')).toBeInTheDocument()
    expect(screen.getByLabelText('Page 5')).toBeInTheDocument()
  })

  it('highlights the current page with aria-current', () => {
    render(<Pagination page={3} totalPages={5} onPageChange={() => {}} />)
    expect(screen.getByLabelText('Page 3')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByLabelText('Page 1')).not.toHaveAttribute('aria-current')
  })

  it('calls onPageChange when a page button is clicked', async () => {
    const user = userEvent.setup()
    const onPageChange = vi.fn()
    render(<Pagination page={1} totalPages={5} onPageChange={onPageChange} />)

    await user.click(screen.getByLabelText('Page 3'))
    expect(onPageChange).toHaveBeenCalledWith(3)
  })

  it('calls onPageChange with page+1 when Next is clicked', async () => {
    const user = userEvent.setup()
    const onPageChange = vi.fn()
    render(<Pagination page={2} totalPages={5} onPageChange={onPageChange} />)

    await user.click(screen.getByRole('button', { name: /next/i }))
    expect(onPageChange).toHaveBeenCalledWith(3)
  })

  it('calls onPageChange with page-1 when Previous is clicked', async () => {
    const user = userEvent.setup()
    const onPageChange = vi.fn()
    render(<Pagination page={3} totalPages={5} onPageChange={onPageChange} />)

    await user.click(screen.getByRole('button', { name: /previous/i }))
    expect(onPageChange).toHaveBeenCalledWith(2)
  })

  it('disables Previous on the first page', () => {
    render(<Pagination page={1} totalPages={5} onPageChange={() => {}} />)
    expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled()
  })

  it('disables Next on the last page', () => {
    render(<Pagination page={5} totalPages={5} onPageChange={() => {}} />)
    expect(screen.getByRole('button', { name: /next/i })).toBeDisabled()
  })

  it('shows ellipsis for many pages when on a middle page', () => {
    render(<Pagination page={5} totalPages={10} onPageChange={() => {}} />)
    const dots = screen.getAllByText('...')
    expect(dots.length).toBeGreaterThanOrEqual(1)
  })

  it('shows all pages when totalPages <= 7', () => {
    render(<Pagination page={1} totalPages={7} onPageChange={() => {}} />)
    for (let i = 1; i <= 7; i++) {
      expect(screen.getByLabelText(`Page ${i}`)).toBeInTheDocument()
    }
    expect(screen.queryByText('...')).not.toBeInTheDocument()
  })

  it('renders the pagination nav with the correct aria-label', () => {
    render(<Pagination page={1} totalPages={3} onPageChange={() => {}} />)
    expect(screen.getByRole('navigation', { name: /pagination/i })).toBeInTheDocument()
  })

  it('renders nothing for zero pages', () => {
    const { container } = render(
      <Pagination page={1} totalPages={0} onPageChange={() => {}} />
    )
    expect(container.querySelector('nav')).toBeNull()
  })
})
