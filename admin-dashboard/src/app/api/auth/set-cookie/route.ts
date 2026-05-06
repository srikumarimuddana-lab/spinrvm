import { NextRequest, NextResponse } from 'next/server';

const COOKIE_NAME = 'admin_token';
const COOKIE_MAX_AGE = 60 * 60 * 8; // 8 hours — matches admin session lifetime

export async function POST(request: NextRequest) {
    let token: string | undefined;
    try {
        const body = await request.json();
        token = body?.token;
        console.log('[POST /api/auth/set-cookie] Received token:', token ? `${token.substring(0, 20)}...` : 'undefined');
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
    console.log('[POST /api/auth/set-cookie] Cookie set successfully. NODE_ENV:', process.env.NODE_ENV, 'secure:', process.env.NODE_ENV === 'production');
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
