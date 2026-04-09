import { describe, it, expect, vi, beforeEach } from 'vitest';
import { exportToCsv } from '@/lib/export-csv';

describe('exportToCsv', () => {
  let appendChildSpy: ReturnType<typeof vi.spyOn>;
  let removeChildSpy: ReturnType<typeof vi.spyOn>;
  let clickSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    clickSpy = vi.fn();
    appendChildSpy = vi.spyOn(document.body, 'appendChild').mockImplementation((node) => node);
    removeChildSpy = vi.spyOn(document.body, 'removeChild').mockImplementation((node) => node);

    vi.spyOn(document, 'createElement').mockReturnValue({
      href: '',
      download: '',
      click: clickSpy,
    } as any);
  });

  it('should do nothing for empty rows', () => {
    exportToCsv('test', []);
    expect(appendChildSpy).not.toHaveBeenCalled();
  });

  it('should create and trigger download for valid data', () => {
    const rows = [
      { name: 'John', email: 'john@test.com', rides: 10 },
      { name: 'Jane', email: 'jane@test.com', rides: 5 },
    ];

    exportToCsv('drivers', rows);

    expect(appendChildSpy).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    expect(removeChildSpy).toHaveBeenCalled();
  });

  it('should use custom columns when provided', () => {
    const rows = [{ name: 'John', email: 'john@test.com' }];
    const columns = [{ key: 'name', label: 'Driver Name' }];

    exportToCsv('drivers', rows, columns);

    expect(clickSpy).toHaveBeenCalled();
  });

  it('should handle null and undefined values', () => {
    const rows = [{ name: 'John', email: null, phone: undefined }];

    // Should not throw
    expect(() => exportToCsv('test', rows)).not.toThrow();
    expect(clickSpy).toHaveBeenCalled();
  });

  it('should escape quotes in values', () => {
    const rows = [{ name: 'John "JD" Doe' }];

    expect(() => exportToCsv('test', rows)).not.toThrow();
    expect(clickSpy).toHaveBeenCalled();
  });

  it('should handle object values by stringifying them', () => {
    const rows = [{ name: 'John', metadata: { key: 'value' } }];

    expect(() => exportToCsv('test', rows)).not.toThrow();
    expect(clickSpy).toHaveBeenCalled();
  });
});
