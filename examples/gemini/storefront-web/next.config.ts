// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  transpilePackages: ["web-shared"],
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Content-Security-Policy",
            value: "frame-ancestors 'self' https://*.lightning.force.com https://*.salesforce.com",
          },
        ],
      },
    ];
  },
  experimental: {
    serverActions: {
      allowedOrigins: [
        "gemini-storefront-web-production.up.railway.app",
        "*.up.railway.app",
        "localhost:3005",
      ],
    },
  },
};

export default nextConfig;
