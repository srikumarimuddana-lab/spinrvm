/**
 * IncidentEvidencePhotos — the admin-side render of safety evidence.
 *
 * The bug this whole feature closes was evidence going missing without anyone
 * noticing (the driver app POSTed photos to a route that did not exist, behind
 * a `catch {}`). So the cases that matter most here are the ones where a photo
 * exists but cannot be shown: those must still surface, never be dropped.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// ---------- stub UI primitives (avoid Radix portal internals) ----------
vi.mock('@/components/ui/button', () => ({
  Button: ({ children, ...p }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...p}>{children}</button>
  ),
}));
vi.mock('@/components/ui/label', () => ({
  Label: ({ children, ...p }: React.PropsWithChildren<React.HTMLAttributes<HTMLLabelElement>>) => (
    <label {...p}>{children}</label>
  ),
}));
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children, open }: React.PropsWithChildren<{ open?: boolean }>) => (open ? <div>{children}</div> : null),
  DialogContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogTitle: ({ children }: React.PropsWithChildren) => <h2>{children}</h2>,
}));
vi.mock('lucide-react', () => ({
  AlertTriangle: () => <span data-testid="warn-icon" />,
  ExternalLink: () => <span data-testid="external-icon" />,
}));

import { IncidentEvidencePhotos } from './incident-evidence-photos';

const signed = (id: string) => ({
  id,
  content_type: 'image/jpeg',
  created_at: '2026-08-18T10:00:00Z',
  url: `https://signed.example/${id}`,
});

const unsignable = (id: string) => ({
  id,
  content_type: 'image/jpeg',
  created_at: '2026-08-18T10:00:00Z',
  url: null,
});

describe('IncidentEvidencePhotos', () => {
  it('renders nothing when the incident has no photos', () => {
    const { container } = render(<IncidentEvidencePhotos photos={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders a thumbnail per photo with the total count', () => {
    render(<IncidentEvidencePhotos photos={[signed('a'), signed('b')]} />);

    expect(screen.getByText('Evidence photos (2)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open evidence photo 1 of 2' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open evidence photo 2 of 2' })).toBeInTheDocument();
    expect(screen.getByAltText('Evidence 1')).toHaveAttribute('src', 'https://signed.example/a');
  });

  it('still shows a tile for a photo whose signed URL could not be minted', () => {
    // The regression guard: silently omitting it would tell a reviewer that no
    // evidence exists when it does.
    render(<IncidentEvidencePhotos photos={[signed('a'), unsignable('b')]} />);

    expect(screen.getByText('Evidence photos (2)')).toBeInTheDocument();
    expect(screen.getByText('Preview unavailable')).toBeInTheDocument();
    // Only the signed one is navigable.
    expect(screen.getByRole('button', { name: 'Open evidence photo 1 of 1' })).toBeInTheDocument();
  });

  it('counts every attached photo even when none can be displayed', () => {
    render(<IncidentEvidencePhotos photos={[unsignable('a'), unsignable('b')]} />);

    expect(screen.getByText('Evidence photos (2)')).toBeInTheDocument();
    expect(screen.getAllByText('Preview unavailable')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: /Open evidence photo/ })).not.toBeInTheDocument();
  });

  it('opens the lightbox on thumbnail click and offers a full-size link', () => {
    render(<IncidentEvidencePhotos photos={[signed('a'), signed('b')]} />);

    expect(screen.queryByText('Evidence photo 1 of 2')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open evidence photo 1 of 2' }));

    expect(screen.getByText('Evidence photo 1 of 2')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open full size/ })).toHaveAttribute(
      'href',
      'https://signed.example/a',
    );
  });

  it('pages between photos in the lightbox, with the ends disabled', () => {
    render(<IncidentEvidencePhotos photos={[signed('a'), signed('b')]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open evidence photo 1 of 2' }));

    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Evidence photo 2 of 2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Previous' })).not.toBeDisabled();
  });

  it('skips unsignable photos when paging — index maps to viewable, not raw list', () => {
    // Ordering matters: an unsignable photo sitting between two signed ones
    // must not shift the lightbox onto a blank entry.
    render(<IncidentEvidencePhotos photos={[signed('a'), unsignable('x'), signed('b')]} />);

    fireEvent.click(screen.getByRole('button', { name: 'Open evidence photo 2 of 2' }));

    // Both the thumbnail and the lightbox carry alt="Evidence 2"; asserting on
    // all of them proves the lightbox landed on 'b' and not the unsignable 'x'.
    const shown = screen.getAllByAltText('Evidence 2');
    expect(shown.length).toBeGreaterThan(1);
    shown.forEach((img) => expect(img).toHaveAttribute('src', 'https://signed.example/b'));
  });

  it('shows the attached-at timestamp using the injected formatter', () => {
    render(
      <IncidentEvidencePhotos photos={[signed('a')]} formatDateTime={() => '18 Aug 2026, 10:00'} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Open evidence photo 1 of 1' }));

    expect(screen.getByText('Attached 18 Aug 2026, 10:00')).toBeInTheDocument();
  });
});
