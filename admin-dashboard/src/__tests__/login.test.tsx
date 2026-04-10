/**
 * Smoke test — Login page (src/app/login/page.tsx).
 *
 * Verifies the page mounts without crashing and renders
 * the expected email, password inputs and a submit button.
 *
 * All external dependencies (Next.js router, Zustand, API) are mocked
 * so no real navigation or network calls occur.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// ---------- mock next/navigation ----------
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// ---------- mock Zustand authStore ----------
vi.mock('@/store/authStore', () => ({
  useAuthStore: () => ({
    token: null,
    setToken: vi.fn(),
    setUser: vi.fn(),
    logout: vi.fn(),
  }),
}));

// ---------- mock api ----------
vi.mock('@/lib/api', () => ({
  loginAdminSession: vi.fn(),
}));

// ---------- stub out UI primitives that use server-only Next.js internals --------
// Some shadcn components use next/font or next/image; mock them away.
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

vi.mock('@/components/ui/card', () => ({
  Card: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  CardContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  CardHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  CardTitle: ({ children }: React.PropsWithChildren) => <h1>{children}</h1>,
}));

// ---------- import the page AFTER all mocks ----------
import LoginPage from '@/app/login/page';

describe('LoginPage smoke test', () => {
  it('renders without crashing', () => {
    expect(() => render(<LoginPage />)).not.toThrow();
  });

  it('shows an email input', () => {
    render(<LoginPage />);
    // The input might use type="email" or a label with "email" text
    const emailInput =
      screen.queryByRole('textbox', { name: /email/i }) ||
      document.querySelector('input[type="email"]') ||
      document.querySelector('input[type="text"]');
    expect(emailInput).toBeTruthy();
  });

  it('shows a password input', () => {
    render(<LoginPage />);
    const passwordInput = document.querySelector('input[type="password"]');
    expect(passwordInput).toBeTruthy();
  });

  it('renders a submit / login button', () => {
    render(<LoginPage />);
    const btn = screen.queryByRole('button');
    expect(btn).toBeTruthy();
  });
});
