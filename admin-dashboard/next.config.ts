import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8400/api/:path*",
      },
      {
        source: "/ws/:path*",
        destination: "http://127.0.0.1:8400/ws/:path*",
      },
    ];
  },
};

export default nextConfig;
