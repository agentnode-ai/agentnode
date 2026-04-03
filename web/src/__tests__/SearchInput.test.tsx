import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SearchInput from '@/components/SearchInput'

describe('SearchInput', () => {
  it('renders with default placeholder', () => {
    render(<SearchInput value="" onChange={() => {}} />)
    expect(screen.getByPlaceholderText('Search packages...')).toBeInTheDocument()
  })

  it('renders with custom placeholder', () => {
    render(<SearchInput value="" onChange={() => {}} placeholder="Find tools..." />)
    expect(screen.getByPlaceholderText('Find tools...')).toBeInTheDocument()
  })

  it('renders with the provided value', () => {
    render(<SearchInput value="web scraper" onChange={() => {}} />)
    expect(screen.getByRole('textbox')).toHaveValue('web scraper')
  })

  it('calls onChange handler when typing', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<SearchInput value="" onChange={onChange} />)

    await user.type(screen.getByRole('textbox'), 'a')
    expect(onChange).toHaveBeenCalledWith('a')
  })

  it('calls onSubmit when Enter is pressed', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<SearchInput value="test" onChange={() => {}} onSubmit={onSubmit} />)

    await user.type(screen.getByRole('textbox'), '{Enter}')
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('does not call onSubmit for non-Enter keys', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<SearchInput value="" onChange={() => {}} onSubmit={onSubmit} />)

    await user.type(screen.getByRole('textbox'), 'abc')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('has the correct aria-label', () => {
    render(<SearchInput value="" onChange={() => {}} />)
    expect(screen.getByLabelText('Search packages')).toBeInTheDocument()
  })

  it('applies large size classes when size="large"', () => {
    render(<SearchInput value="" onChange={() => {}} size="large" />)
    const input = screen.getByRole('textbox')
    expect(input.className).toContain('h-14')
    expect(input.className).toContain('text-lg')
  })

  it('applies default size classes when size is not specified', () => {
    render(<SearchInput value="" onChange={() => {}} />)
    const input = screen.getByRole('textbox')
    expect(input.className).toContain('h-11')
    expect(input.className).toContain('text-sm')
  })

  it('sets autoFocus when prop is true', () => {
    render(<SearchInput value="" onChange={() => {}} autoFocus />)
    const input = screen.getByRole('textbox')
    // React's autoFocus triggers focus in the DOM, not an HTML attribute
    expect(document.activeElement).toBe(input)
  })
})
