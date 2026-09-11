import { timingSafeEqual } from 'crypto';
import { NextRequest, NextResponse } from 'next/server';

const COOKIE_NAME = 'admin_token';
const COOKIE_MAX_AGE = 8 * 60 * 60; // 8 hours — admin session TTL (P0 security hardening)
// Same double-submit cookie the /api/admin/auth/{login,refresh,mfa/challenge}
// routes already set on every successful auth — by the time the client calls
// this route (always right after one of those), the cookie is already
// present. See routes/admin/auth/refresh/route.ts's verifyCsrf for the twin
// implementation this mirrors.
const CSRF_COOKIE = 'spinr_admin_csrf';

// Admin RBAC/security audit finding W6 (docs/audit/2026-09-10-admin-portal-
// security-rbac-audit.md): this route used to accept any JSON body from any
// origin with no CSRF/Origin check, letting a cross-site page overwrite the
// admin_token cookie with an attacker-chosen value (text/plain is a CORS-
// simple content type, and request.json() still parses a JSON-shaped
// text/plain body). Today's impact was bounded — nothing server-side trusts
// this cookie for a real API call, only the edge middleware's client-side
// redirect gate — but any future SSR/internal-API code that starts trusting
// it would become an instant full auth bypass with no review signal that
// this endpoint was unprotected. Every other state-changing BFF route in
// this app already requires the double-submit CSRF token; this one now does
// too.
function verifyCsrf(req: NextRequest): boolean {
    const cookieToken = req.cookies.get(CSRF_COOKIE)?.value;
    const headerToken = req.headers.get('x-csrf-token');
    if (!cookieToken || !headerToken) return false;
    try {
        const a = Buffer.from(cookieToken, 'utf8');
        const b = Buffer.from(headerToken, 'utf8');
        // timingSafeEqual requires equal-length buffers; mismatched length → reject.
        if (a.length !== b.length) return false;
        return timingSafeEqual(a, b);
    } catch {
        return false;
    }
}

export async function POST(request: NextRequest) {
    if (!verifyCsrf(request)) {
        return NextResponse.json({ error: 'CSRF validation failed' }, { status: 403 });
    }
    let token: string | undefined;
    try {
        const body = await request.json();
        token = body?.token;
    } catch (error) {
        console.error('[POST /api/auth/set-cookie] JSON parse error:', error);
        return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }
    if (!token || typeof token !== 'string') {
        console.error('[POST /api/auth/set-cookie] Token validation failed:', { token, type: typeof token });
        return NextResponse.json({ error: 'Missing token' }, { status: 400 });
    }

    const response = NextResponse.json({ ok: true });
    response.cookies.set(COOKIE_NAME, token, {
        httpOnly: true,
        secure: process.env.NODE_ENV === 'production',
        sameSite: 'strict',
        maxAge: COOKIE_MAX_AGE,
        path: '/',
    });
    return response;
}

export async function DELETE() {
    const response = NextResponse.json({ ok: true });
    response.cookies.set(COOKIE_NAME, '', {
        httpOnly: true,
        secure: process.env.NODE_ENV === 'production',
        sameSite: 'strict',
        maxAge: 0,
        path: '/',
    });
    return response;
}
