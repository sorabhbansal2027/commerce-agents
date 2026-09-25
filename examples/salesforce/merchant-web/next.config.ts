// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  transpilePackages: ["web-shared"],
  // Next.js 15/16 validates the Host header in production.
  // Allow the Railway public domain and any BM/SFCC origins.
  serverExternalPackages: [],
  experimental: {
    serverActions: {
      allowedOrigins: [
        "merchant-web-production-e806.up.railway.app",
        "*.up.railway.app",
        "*.commercecloud.salesforce.com",
        "*.salesforce.com",
        "localhost:3105",
      ],
    },
  },
  async headers() {
    // Allow SFCC Business Manager to embed this app in an iframe.
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "ALLOWALL" },
          { key: "Content-Security-Policy", value: "frame-ancestors *" },
        ],
      },
    ];
  },
};

export default nextConfig;
