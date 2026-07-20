/**
 * DocumentUploadDialog — bulk upload, per-file type mapping, on-file dates.
 *
 * Covers the admin request: pick multiple files at once, map each to a
 * document type, and see the dates already recorded for types that have them.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';

const adminUploadDriverDocument = vi.fn().mockResolvedValue({});
vi.mock('@/lib/api', () => ({
  adminUploadDriverDocument: (...a: unknown[]) => adminUploadDriverDocument(...a),
}));

// ---------- stub UI primitives (avoid Radix portal / next internals) ----------
vi.mock('@/components/ui/button', () => ({
  Button: ({ children, ...p }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...p}>{children}</button>
  ),
}));
vi.mock('@/components/ui/input', () => {
  // forwardRef so the dialog's fileInputRef works.
  const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>((p, ref) => (
    <input ref={ref} {...p} />
  ));
  Input.displayName = 'Input';
  return { Input };
});
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children, open }: React.PropsWithChildren<{ open?: boolean }>) => (open ? <div>{children}</div> : null),
  DialogContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogTitle: ({ children }: React.PropsWithChildren) => <h2>{children}</h2>,
  DialogDescription: ({ children }: React.PropsWithChildren) => <p>{children}</p>,
  DialogFooter: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}));
vi.mock('@/components/ui/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

// Native-select stand-in for the shadcn/Radix Select so tests can drive it.
vi.mock('@/components/ui/select', () => ({
  Select: ({ value, onValueChange, children }: React.PropsWithChildren<{ value?: string; onValueChange?: (v: string) => void }>) => (
    <select data-testid="select" value={value} onChange={(e) => onValueChange?.(e.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: ({ children }: React.PropsWithChildren) => <>{children}</>,
  SelectValue: () => null,
  SelectContent: ({ children }: React.PropsWithChildren) => <>{children}</>,
  SelectItem: ({ value, children }: React.PropsWithChildren<{ value: string }>) => (
    <option value={value}>{children}</option>
  ),
}));

import { DocumentUploadDialog, UploadRequirement, ExistingDocInfo } from './document-upload-dialog';

const requirements: UploadRequirement[] = [
  { key: 'drivers_license', label: "Driver's License", has_expiry: true },
  { key: 'vehicle_insurance', label: 'Vehicle Insurance', has_expiry: true },
];

const existing: Record<string, ExistingDocInfo> = {
  drivers_license: { expiry: '2027-05-01', status: 'approved' },
};

function selectType(label: string, value: string) {
  const typeSelect = screen.getAllByTestId('select').find((s) => within(s).queryByText(label));
  if (!typeSelect) throw new Error(`no type select listing ${label}`);
  fireEvent.change(typeSelect, { target: { value } });
}

function selectFiles(names: string[]) {
  const input = document.getElementById('upload-files') as HTMLInputElement;
  const files = names.map((n) => new File(['x'], n, { type: 'application/pdf' }));
  fireEvent.change(input, { target: { files } });
}

beforeEach(() => adminUploadDriverDocument.mockClear());

describe('DocumentUploadDialog', () => {
  it('shows the date already on file for a type that has one', () => {
    render(
      <DocumentUploadDialog open onClose={() => {}} driverId="d1" driverName="Ada"
        requirements={requirements} existing={existing} />,
    );
    expect(screen.getByText(/Already on file/i)).not.toBeNull();
    // 2027-05-01 formatted as a localized date.
    expect(screen.getByText(/expires .*2027/i)).not.toBeNull();
  });

  it('uploads multiple mapped files in one action', async () => {
    render(
      <DocumentUploadDialog open onClose={() => {}} driverId="d1" driverName="Ada"
        requirements={requirements} existing={existing} />,
    );

    selectFiles(['license.pdf', 'insurance.pdf']);

    // One row per file. The type selects are the ones listing document types;
    // the status selects (also mocked <select>s) list "Pending/Approved".
    const typeSelects = screen
      .getAllByTestId('select')
      .filter((s) => within(s).queryByText("Driver's License"));
    expect(typeSelects).toHaveLength(2);
    fireEvent.change(typeSelects[0], { target: { value: 'drivers_license' } });
    fireEvent.change(typeSelects[1], { target: { value: 'vehicle_insurance' } });

    fireEvent.click(screen.getByRole("button", { name: /Upload/ }));

    await waitFor(() => expect(adminUploadDriverDocument).toHaveBeenCalledTimes(2));
    const keys = adminUploadDriverDocument.mock.calls.map((c) => c[0].requirementKey).sort();
    expect(keys).toEqual(['drivers_license', 'vehicle_insurance']);
  });

  it('blocks upload until every file is mapped to a type', () => {
    render(
      <DocumentUploadDialog open onClose={() => {}} driverId="d1" driverName="Ada"
        requirements={requirements} existing={existing} />,
    );
    selectFiles(['mystery.pdf']);
    // Unmapped → guard message shown and upload does nothing.
    expect(screen.getByText(/Map every file/i)).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Upload/ }));
    expect(adminUploadDriverDocument).not.toHaveBeenCalled();
  });

  it('prefills the on-file expiry date when a type is selected', () => {
    render(
      <DocumentUploadDialog open onClose={() => {}} driverId="d1" driverName="Ada"
        requirements={requirements} existing={existing} />,
    );
    selectFiles(['license.pdf']);
    selectType("Driver's License", 'drivers_license');
    // drivers_license already has expiry 2027-05-01 on file → the date input is
    // seeded with it instead of starting empty.
    const dateInput = screen.getByLabelText(/Expiry date for license.pdf/i) as HTMLInputElement;
    expect(dateInput.value).toBe('2027-05-01');
  });

  it('does not prefill an expiry for a type that has none on file', () => {
    render(
      <DocumentUploadDialog open onClose={() => {}} driverId="d1" driverName="Ada"
        requirements={requirements} existing={existing} />,
    );
    selectFiles(['insurance.pdf']);
    // vehicle_insurance has no on-file expiry → the date input stays empty.
    selectType('Vehicle Insurance', 'vehicle_insurance');
    const dateInput = screen.getByLabelText(/Expiry date for insurance.pdf/i) as HTMLInputElement;
    expect(dateInput.value).toBe('');
  });
});
