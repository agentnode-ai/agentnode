import { describe, it, expect } from 'vitest'
import { SLUG_PATTERN, DEFAULT_GUIDED, EMPTY_TOOL } from '@/app/publish/lib/constants'
import type { GuidedState } from '@/app/publish/lib/types'

/**
 * These tests cover the publish form validation logic:
 * - SLUG_PATTERN regex for package_id validation
 * - DEFAULT_GUIDED shape and defaults
 * - EMPTY_TOOL shape
 *
 * We test the validation primitives rather than rendering the full publish form,
 * because the publish page depends on a complex hook (usePublishForm) with
 * auth checks, sessionStorage, and multiple API calls.
 */

describe('Publish form validation: SLUG_PATTERN', () => {
  it('accepts a valid lowercase slug', () => {
    expect(SLUG_PATTERN.test('web-scraper')).toBe(true)
  })

  it('accepts a numeric slug', () => {
    expect(SLUG_PATTERN.test('tool123')).toBe(true)
  })

  it('accepts a slug with hyphens', () => {
    expect(SLUG_PATTERN.test('my-cool-tool')).toBe(true)
  })

  it('accepts a 3-character slug (minimum length)', () => {
    expect(SLUG_PATTERN.test('abc')).toBe(true)
  })

  it('accepts a 60-character slug (maximum length)', () => {
    const slug = 'a'.repeat(60)
    expect(SLUG_PATTERN.test(slug)).toBe(true)
  })

  it('rejects a slug shorter than 3 characters', () => {
    expect(SLUG_PATTERN.test('ab')).toBe(false)
  })

  it('rejects a slug longer than 60 characters', () => {
    const slug = 'a'.repeat(61)
    expect(SLUG_PATTERN.test(slug)).toBe(false)
  })

  it('rejects uppercase letters', () => {
    expect(SLUG_PATTERN.test('Web-Scraper')).toBe(false)
  })

  it('rejects spaces', () => {
    expect(SLUG_PATTERN.test('web scraper')).toBe(false)
  })

  it('rejects underscores', () => {
    expect(SLUG_PATTERN.test('web_scraper')).toBe(false)
  })

  it('rejects special characters', () => {
    expect(SLUG_PATTERN.test('web@scraper')).toBe(false)
    expect(SLUG_PATTERN.test('web.scraper')).toBe(false)
    expect(SLUG_PATTERN.test('web/scraper')).toBe(false)
  })

  it('rejects an empty string', () => {
    expect(SLUG_PATTERN.test('')).toBe(false)
  })
})

describe('Publish form validation: DEFAULT_GUIDED', () => {
  it('has an empty name by default', () => {
    expect(DEFAULT_GUIDED.name).toBe('')
  })

  it('has an empty package_id by default', () => {
    expect(DEFAULT_GUIDED.package_id).toBe('')
  })

  it('defaults to toolpack package_type', () => {
    expect(DEFAULT_GUIDED.package_type).toBe('toolpack')
  })

  it('defaults version to 1.0.0', () => {
    expect(DEFAULT_GUIDED.version).toBe('1.0.0')
  })

  it('has one empty tool by default', () => {
    expect(DEFAULT_GUIDED.tools).toHaveLength(1)
    expect(DEFAULT_GUIDED.tools[0].name).toBe('')
  })

  it('defaults frameworks to ["generic"]', () => {
    expect(DEFAULT_GUIDED.frameworks).toEqual(['generic'])
  })

  it('defaults all permissions to restrictive values', () => {
    expect(DEFAULT_GUIDED.network).toBe('none')
    expect(DEFAULT_GUIDED.filesystem).toBe('none')
    expect(DEFAULT_GUIDED.code_execution).toBe('none')
    expect(DEFAULT_GUIDED.data_access).toBe('input_only')
  })
})

describe('Publish form validation: EMPTY_TOOL', () => {
  it('has empty strings for all fields', () => {
    expect(EMPTY_TOOL.name).toBe('')
    expect(EMPTY_TOOL.description).toBe('')
    expect(EMPTY_TOOL.capability_id).toBe('')
    expect(EMPTY_TOOL.entrypoint).toBe('')
    expect(EMPTY_TOOL.input_schema).toBe('')
    expect(EMPTY_TOOL.output_schema).toBe('')
  })
})

describe('Publish form validation: GuidedState shape', () => {
  it('DEFAULT_GUIDED satisfies the GuidedState type', () => {
    // This is a compile-time check: if DEFAULT_GUIDED doesn't satisfy GuidedState,
    // TypeScript will produce an error. At runtime, just verify it has all keys.
    const guided: GuidedState = { ...DEFAULT_GUIDED }
    expect(guided).toBeDefined()
    expect(typeof guided.name).toBe('string')
    expect(typeof guided.package_id).toBe('string')
    expect(typeof guided.version).toBe('string')
    expect(typeof guided.summary).toBe('string')
    expect(typeof guided.description).toBe('string')
    expect(Array.isArray(guided.tools)).toBe(true)
    expect(Array.isArray(guided.frameworks)).toBe(true)
    expect(Array.isArray(guided.use_cases)).toBe(true)
    expect(Array.isArray(guided.examples)).toBe(true)
    expect(Array.isArray(guided.env_requirements)).toBe(true)
  })
})
