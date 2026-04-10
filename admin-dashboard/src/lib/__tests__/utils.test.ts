import { describe, it, expect } from 'vitest';
import { cn, formatCurrency, formatDate } from '../utils';

describe('cn()', () => {
  it('returns a single class unchanged', () => {
    expect(cn('foo')).toBe('foo');
  });

  it('merges multiple classes', () => {
    const result = cn('flex', 'items-center', 'gap-2');
    expect(result).toBe('flex items-center gap-2');
  });

  it('resolves Tailwind conflicts (last one wins)', () => {
    // twMerge should keep the last padding utility
    const result = cn('p-2', 'p-4');
    expect(result).toBe('p-4');
  });

  it('filters out falsy values', () => {
    const result = cn('foo', false && 'bar', undefined, null as any, 'baz');
    expect(result).toBe('foo baz');
  });
});

describe('formatCurrency()', () => {
  it('formats zero', () => {
    expect(formatCurrency(0)).toContain('0');
  });

  it('formats a whole dollar amount in CAD style', () => {
    const result = formatCurrency(10);
    expect(result).toContain('10');
    // en-CA CAD formatting includes a dollar sign
    expect(result).toMatch(/\$/);
  });

  it('formats a decimal amount with two decimal places', () => {
    const result = formatCurrency(12.5);
    expect(result).toContain('12');
    expect(result).toContain('50');
  });

  it('formats a negative value', () => {
    const result = formatCurrency(-5);
    expect(result).toContain('5');
  });
});

describe('formatDate()', () => {
  it('returns em dash for null', () => {
    expect(formatDate(null)).toBe('—');
  });

  it('returns em dash for undefined', () => {
    expect(formatDate(undefined)).toBe('—');
  });

  it('returns em dash for empty string', () => {
    expect(formatDate('')).toBe('—');
  });

  it('formats a valid ISO date string', () => {
    const result = formatDate('2026-04-09T12:00:00Z');
    // Should contain the year
    expect(result).toContain('2026');
    // Should contain the month abbreviation
    expect(result).toMatch(/Apr/);
  });

  it('formats a Date object', () => {
    const d = new Date('2026-01-15T08:30:00Z');
    const result = formatDate(d);
    expect(result).toContain('2026');
  });
});
