// Test script to verify the admin login flow against the Railway backend.
// Usage:
//   npx tsx admin-dashboard/scripts/test-railway-login.ts <email> <password>
//   BACKEND_URL=http://localhost:8000 npx tsx admin-dashboard/scripts/test-railway-login.ts <email> <password>

import { redactEmail } from '../../shared/utils/pii';

const BACKEND_URL =
  process.env.BACKEND_URL ?? 'https://spinr-backend-production.up.railway.app';

async function testAdminLogin(email: string, password: string) {
  console.log(`\n🔍 Testing Admin Login Flow against ${BACKEND_URL}`);
  
  // 1. Attempt Login
  console.log(`\n➡️ POST /api/admin/auth/login with email: ${redactEmail(email)}`);
  const loginRes = await fetch(`${BACKEND_URL}/api/admin/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password })
  });

  if (!loginRes.ok) {
    const errorText = await loginRes.text();
    console.error(`❌ Login failed! Status: ${loginRes.status}`);
    console.error(`Response: ${errorText}`);
    console.log('\n💡 Make sure you are using the correct credentials for the production DB.');
    process.exit(1);
  }

  const loginData = await loginRes.json();
  const token = loginData.token;
  
  if (!token) {
    console.error('❌ Login successful but no token received!');
    console.error('Response:', loginData);
    process.exit(1);
  }

  console.log(`✅ Login successful!`);
  console.log(`🔑 Received Access Token: ${token.substring(0, 15)}...`);
  console.log(`👤 User Role: ${loginData.user?.role}`);
  console.log(`🛡️ Modules: ${loginData.user?.modules?.join(', ')}`);

  // 2. Check Session (Verifies the JWT audience issue is fixed)
  console.log(`\n➡️ GET /api/admin/auth/session to verify token decoding`);
  const sessionRes = await fetch(`${BACKEND_URL}/api/admin/auth/session`, {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${token}`
    }
  });

  if (!sessionRes.ok) {
    const errorText = await sessionRes.text();
    console.error(`❌ Session check failed! Status: ${sessionRes.status}`);
    console.error(`Response: ${errorText}`);
    console.log('\n💡 This usually means the JWT token decoding failed (e.g. InvalidAudienceError).');
    process.exit(1);
  }

  const sessionData = await sessionRes.json();
  
  if (sessionData.authenticated) {
    console.log(`✅ Session verified successfully!`);
    console.log(`👤 Authenticated User: ${redactEmail(sessionData.user?.email)}`);
    console.log(`\n🎉 The admin JWT flow is working perfectly against the Railway backend!`);
  } else {
    console.error(`❌ Session check returned 200 OK but authenticated=false!`);
    console.error('Response:', sessionData);
    process.exit(1);
  }
}

// Run the script
const email = process.argv[2] || 'admin@spinr.ca';
const password = process.argv[3] || 'Admin12345';

testAdminLogin(email, password).catch(err => {
  console.error('\n💥 Unexpected script error:', err.message);
  process.exit(1);
});
