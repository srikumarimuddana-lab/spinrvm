import { describe, it, expect } from 'vitest';
import { cn, formatCurrency, formatDate, statusColor } from '@/lib/utils';

describe('cn (className merge)', () => {
  it('should merge class names', () => {
    expect(cn('px-4', 'py-2')).toBe('px-4 py-2');
  });

  it('should handle conflicting tailwind classes', () => {
    expect(cn('px-4', 'px-6')).toBe('px-6');
  });

  it('should handle conditional classes', () => {
    expect(cn('base', false && 'hidden', 'extra')).toBe('base extra');
  });

  it('should handle undefined/null inputs', () => {
    expect(cn('base', undefined, null)).toBe('base');
  });
});

describe('formatCurrency', () => {
  it('should format a number as CAD currency', () => {
    const result = formatCurrency(15.5);
    expect(result).toContain('15.50');
    expect(result).toContain('$');
  });

  it('should format zero', () => {
    const result = formatCurrency(0);
    expect(result).toContain('0.00');
  });

  it('should format large amounts', () => {
    const result = formatCurrency(1250.99);
    expect(result).toContain('1,250.99');
  });

  it('should format negative amounts', () => {
    const result = formatCurrency(-10);
    expect(result).toContain('10.00');
  });
});

describe('formatDate', () => {
  it('should format a valid date string', () => {
    const result = formatDate('2024-06-15T14:30:00Z');
    expect(result).not.toBe('—');
    expect(typeof result).toBe('string');
  });

  it('should return dash for null', () => {
    expect(formatDate(null)).toBe('—');
  });

  it('should return dash for undefined', () => {
    expect(formatDate(undefined)).toBe('—');
  });

  it('should handle Date objects', () => {
    const result = formatDate(new Date('2024-01-15'));
    expect(result).not.toBe('—');
  });
});

describe('statusColor', () => {
  it('should return correct color for searching status', () => {
    const result = statusColor('searching');
    expect(result).toContain('yellow');
  });

  it('should return correct color for completed status', () => {
    const result = statusColor('completed');
    expect(result).toContain('green');
  });

  it('should return correct color for cancelled status', () => {
    const result = statusColor('cancelled');
    expect(result).toContain('red');
  });

  it('should return correct color for in_progress status', () => {
    const result = statusColor('in_progress');
    expect(result).toContain('emerald');
  });

  it('should return default color for unknown status', () => {
    const result = statusColor('unknown_status');
    expect(result).toContain('zinc');
  });
});
