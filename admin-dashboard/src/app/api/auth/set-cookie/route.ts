import { NextRequest, NextResponse } from 'next/server';

const COOKIE_NAME = 'admin_token';
const COOKIE_MAX_AGE = 7 * 24 * 60 * 60; // 7 days — matches login route SESSION_MAX_AGE

export async function POST(request: NextRequest) {
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
