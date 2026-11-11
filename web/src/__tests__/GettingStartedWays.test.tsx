/**
 * Task #7: the website names the three ways to run AgentNode and says honestly how far each one is.
 *
 * The statuses are the ones docs/sandbox/availability.md derives from the code (SDK 0.25.0):
 *   - on your own computer: available, tested on a Linux PC; Windows and macOS available but not
 *     tested here;
 *   - on your own server: available in the SDK on main (0.25.0), NOT in the release on PyPI (0.24.1
 *     has no `agentnode gateway`), and not tested here;
 *   - the hosted cloud sandbox: not accepted -- its two-machine acceptance is still open -- so it is
 *     not offered as a way anyone can use today.
 * And the home page no longer says "No cloud bill", which an offered cloud sandbox contradicts.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import fs from 'fs'
import path from 'path'

import GettingStartedPage from '@/app/getting-started/page'

function way(name: RegExp) {
  render(<GettingStartedPage />)
  const heading = screen.getByRole('heading', { name })
  const card = heading.closest('[data-way]') as HTMLElement
  expect(card).not.toBeNull()
  return card
}

describe('Getting started: the three ways to run it', () => {
  it('names all three ways under one section', () => {
    render(<GettingStartedPage />)
    expect(screen.getByRole('heading', { name: /where your code runs/i })).toBeTruthy()
    expect(document.querySelectorAll('[data-way]').length).toBe(3)
  })

  it('on your own computer: available, tested on Linux, not claimed tested elsewhere', () => {
    const card = way(/on your own computer/i)
    expect(card.getAttribute('data-status')).toBe('available')
    expect(within(card).getByText(/tested on a linux pc/i)).toBeTruthy()
    expect(within(card).getByText(/windows and macos.*not tested/i)).toBeTruthy()
    expect(within(card).getByText(/pip install agentnode-sdk/)).toBeTruthy()
  })

  it('on your own server: says it is not in the released package yet', () => {
    const card = way(/on your own server/i)
    expect(card.getAttribute('data-status')).toBe('next-release')
    expect(within(card).getByText(/not yet on pypi/i)).toBeTruthy()
    expect(within(card).getByText(/0\.25/)).toBeTruthy()
    const guide = within(card).getByRole('link', { name: /self-hosted guide/i })
    expect(guide.getAttribute('href')).toMatch(/sdk\/docs\/self-hosted-sandbox\.md$/)
  })

  it('the hosted cloud sandbox is visibly marked as not yet accepted and offers no way in', () => {
    const card = way(/hosted cloud sandbox/i)
    expect(card.getAttribute('data-status')).toBe('not-accepted')
    expect(within(card).getByText(/not yet accepted/i)).toBeTruthy()
    expect(within(card).queryByText(/pip install|sign up|get started/i)).toBeNull()
  })
})

describe('Home page', () => {
  it('no longer promises "No cloud bill"', () => {
    const source = fs.readFileSync(path.resolve(__dirname, '../app/page.tsx'), 'utf-8')
    expect(source).not.toMatch(/no cloud bill/i)
  })
})
