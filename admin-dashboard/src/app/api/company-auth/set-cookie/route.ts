import { NextRequest, NextResponse } from 'next/server';

// Company-portal twin of /api/auth/set-cookie: holds the RIDER access token
// for the edge middleware's exp check on /company-portal/**. Rider access
// tokens live 15 minutes; silentRefresh() rewrites this cookie on every
// rotation, so the maxAge only needs to outlive one token's lifetime.
const COOKIE_NAME = 'company_token';
const COOKIE_MAX_AGE = 30 * 60;

export async function POST(request: NextRequest) {
    let token: string | undefined;
    try {
        const body = await request.json();
        token = body?.token;
    } catch (error) {
        console.error('[POST /api/company-auth/set-cookie] JSON parse error:', error);
        return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }
    if (!token || typeof token !== 'string') {
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
