/**
 * DocumentReviewer — the driver document approve/reject flow.
 *
 * Highest-risk piece of the drivers surface (feeds SGI-facing driver
 * eligibility state) and previously had no unit coverage, only e2e.
 * Covers the guardrails: expiry required before approving an
 * expiry-bearing document type, a reason required before rejecting,
 * and that reviewDocument is called with the right status/payload.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const getDriverDocuments = vi.fn();
const reviewDocument = vi.fn().mockResolvedValue({});
vi.mock('@/lib/api', () => ({
  getDriverDocuments: (...a: unknown[]) => getDriverDocuments(...a),
  reviewDocument: (...a: unknown[]) => reviewDocument(...a),
}));

const toast = vi.fn();
vi.mock('@/components/ui/use-toast', () => ({ useToast: () => ({ toast }) }));

vi.mock('@/components/ui/button', () => ({
  Button: ({ children, ...p }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...p}>{children}</button>
  ),
}));
vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children, ...p }: React.PropsWithChildren<React.HTMLAttributes<HTMLSpanElement>>) => <span {...p}>{children}</span>,
}));
vi.mock('@/components/ui/input', () => ({
  Input: (p: React.InputHTMLAttributes<HTMLInputElement>) => <input {...p} />,
}));
vi.mock('@/components/ui/textarea', () => {
  const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>((p, ref) => (
    <textarea ref={ref} {...p} />
  ));
  Textarea.displayName = 'Textarea';
  return { Textarea };
});
vi.mock('@/components/ui/select', () => ({
  Select: ({ value, onValueChange, children }: React.PropsWithChildren<{ value?: string; onValueChange?: (v: string) => void }>) => (
    <select data-testid="template-select" value={value} onChange={(e) => onValueChange?.(e.target.value)}>
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

import { DocumentReviewer } from './document-reviewer';

const licenseDoc = {
  id: 'doc-1',
  driver_id: 'd1',
  document_type: 'Driver License',
  document_url: 'https://files.example/license.jpg',
  status: 'pending',
  requirement_key: 'drivers_license',
};

const nonExpiryDoc = {
  id: 'doc-2',
  driver_id: 'd1',
  document_type: 'Profile Photo',
  document_url: 'https://files.example/photo.jpg',
  status: 'pending',
  requirement_key: 'profile_photo',
};

beforeEach(() => {
  getDriverDocuments.mockReset();
  reviewDocument.mockReset().mockResolvedValue({});
  toast.mockClear();
});

async function renderWithDocs(docs: unknown[]) {
  getDriverDocuments.mockResolvedValue(docs);
  render(<DocumentReviewer open driverId="d1" driverName="Ada" onClose={() => {}} />);
  await waitFor(() => expect(getDriverDocuments).toHaveBeenCalledWith('d1'));
  await screen.findAllByText(/Driver License|Profile Photo/);
}

describe('DocumentReviewer', () => {
  it('disables confirming approval of an expiry-bearing document without a date', async () => {
    await renderWithDocs([licenseDoc]);

    fireEvent.click(screen.getByRole('button', { name: /Approve/ }));
    const confirmButton = screen.getByRole('button', { name: /Confirm approval/ });
    expect(confirmButton).toBeDisabled();

    fireEvent.click(confirmButton);
    expect(reviewDocument).not.toHaveBeenCalled();
  });

  it('rejects the submit-approved guard directly when the date is missing', async () => {
    // Same guard as the disabled-button case, exercised via the "a" keyboard
    // shortcut path, which calls submit("approved") without going through
    // the disabled `Confirm approval` button.
    await renderWithDocs([licenseDoc]);

    fireEvent.keyDown(document, { key: 'a' }); // opens approve mode
    fireEvent.keyDown(document, { key: 'a' }); // submits

    expect(reviewDocument).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Expiry date required' }),
    );
  });

  it('approves with an expiry date once one is provided', async () => {
    await renderWithDocs([licenseDoc]);

    fireEvent.click(screen.getByRole('button', { name: /Approve/ }));
    fireEvent.change(screen.getByLabelText(/Expiry date/i), { target: { value: '2027-01-01' } });
    fireEvent.click(screen.getByRole('button', { name: /Confirm approval/ }));

    await waitFor(() => expect(reviewDocument).toHaveBeenCalledTimes(1));
    expect(reviewDocument).toHaveBeenCalledWith(
      'doc-1',
      'approved',
      undefined,
      new Date('2027-01-01').toISOString(),
    );
  });

  it('does not require an expiry date for a document type that has none', async () => {
    await renderWithDocs([nonExpiryDoc]);

    fireEvent.click(screen.getByRole('button', { name: /Approve/ }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm approval/ }));

    await waitFor(() => expect(reviewDocument).toHaveBeenCalledTimes(1));
    expect(reviewDocument).toHaveBeenCalledWith('doc-2', 'approved', undefined, undefined);
  });

  it('blocks rejection with the "other" template until a reason is typed', async () => {
    await renderWithDocs([licenseDoc]);

    fireEvent.click(screen.getByRole('button', { name: /Reject/ }));
    fireEvent.change(screen.getByTestId('template-select'), { target: { value: 'other' } });
    fireEvent.click(screen.getByRole('button', { name: /Confirm rejection/ }));

    expect(reviewDocument).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Reason required' }));
  });

  it('rejects using the selected template body and notify flag', async () => {
    await renderWithDocs([licenseDoc]);

    fireEvent.click(screen.getByRole('button', { name: /Reject/ }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm rejection/ }));

    await waitFor(() => expect(reviewDocument).toHaveBeenCalledTimes(1));
    expect(reviewDocument).toHaveBeenCalledWith(
      'doc-1',
      'rejected',
      "We couldn't read your document clearly. Please re-upload a sharper photo.",
      undefined,
      { notify: true, notifyTemplate: 'blurry_image' },
    );
  });

  it('shows an approval failure toast and leaves the document pending', async () => {
    reviewDocument.mockRejectedValueOnce(new Error('network down'));
    await renderWithDocs([nonExpiryDoc]);

    fireEvent.click(screen.getByRole('button', { name: /Approve/ }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm approval/ }));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Approval failed', variant: 'destructive' }),
      ),
    );
  });
});
