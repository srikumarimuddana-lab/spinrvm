import os
import socket
from urllib.parse import urlparse

url = os.environ.get("SUPABASE_URL")
if not url:
    raise SystemExit("SUPABASE_URL must be set; this test does not hardcode hostnames")

host = urlparse(url).hostname
try:
    result = socket.getaddrinfo(host, 443)
    print("DNS resolution successful")
    print(f"Address info: {result[0]}")
except Exception as e:
    print(f"DNS resolution failed: {e}")
