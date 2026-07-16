/**
 * MfaEnrollDialog — QR-code enrollment.
 *
 * Regression: the setup step told users to "scan the key below" but rendered
 * only the secret string and a dead `otpauth://` link (unopenable on desktop).
 * There was nothing an authenticator app could scan. This asserts that after
 * starting enrollment a scannable QR code (an <svg>) is shown, the manual key
 * remains as a fallback, and the old broken link is gone.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const OTPAUTH_URI = 'otpauth://totp/Spinr%20Admin:ops@spinr.ca?secret=JBSWY3DPEHPK3PXP&issuer=Spinr%20Admin';
const SECRET = 'JBSWY3DPEHPK3PXP';

// ---------- mock api ----------
const mfaEnroll = vi.fn().mockResolvedValue({ secret: SECRET, otpauth_uri: OTPAUTH_URI });
vi.mock('@/lib/api', () => ({
  mfaEnroll: (...args: unknown[]) => mfaEnroll(...args),
  mfaConfirm: vi.fn(),
}));

// ---------- stub UI primitives (avoid Radix portal / next internals) ----------
vi.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...props}>{children}</button>
  ),
}));
vi.mock('@/components/ui/input', () => ({
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}));
vi.mock('@/components/ui/label', () => ({
  Label: ({ children, ...props }: React.PropsWithChildren<React.LabelHTMLAttributes<HTMLLabelElement>>) => (
    <label {...props}>{children}</label>
  ),
}));
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children, open }: React.PropsWithChildren<{ open?: boolean }>) => (open ? <div>{children}</div> : null),
  DialogContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogTitle: ({ children }: React.PropsWithChildren) => <h2>{children}</h2>,
  DialogDescription: ({ children }: React.PropsWithChildren) => <p>{children}</p>,
}));
vi.mock('@/components/ui/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import { MfaEnrollDialog } from './mfa-enroll-dialog';

describe('MfaEnrollDialog', () => {
  it('renders a scannable QR code (and the manual key) after enrollment starts', async () => {
    render(
      <MfaEnrollDialog open onOpenChange={() => {}} onEnrolled={() => {}} />,
    );

    // Before starting there is no QR to scan.
    expect(document.querySelector('svg')).toBeNull();

    fireEvent.click(screen.getByText('Get Setup Key'));

    // Once enrollment resolves, the QR <svg> is rendered from the otpauth URI.
    // (findByText / getByText throw if absent — asserting not-null keeps the
    // matcher set tsc-safe without the jest-dom type augmentation.)
    expect(await screen.findByText(/Scan with/i)).not.toBeNull();
    expect(document.querySelector('svg')).not.toBeNull();

    // Manual key remains as a fallback for "can't scan".
    expect(screen.getByText(SECRET)).not.toBeNull();

    // The old dead desktop link must be gone.
    expect(screen.queryByText(/Open authenticator link/i)).toBeNull();
  });
});
