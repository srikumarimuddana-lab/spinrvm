import socket

try:
    result = socket.getaddrinfo("dbbadhihiwztmnqnbdke.supabase.co", 443)
    print("DNS resolution successful")
    print(f"Address info: {result[0]}")
except Exception as e:
    print(f"DNS resolution failed: {e}")
