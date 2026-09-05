import type { NextConfig } from "next";

/**
 * The browser talks to `/api/*` on its own origin and Next proxies through to the
 * backend. That keeps CORS out of the picture entirely and, more usefully, means
 * raw media and ZIP bundles are same-origin — so `<video>` seeking and
 * download-triggering anchors behave without preflight requests or exposed-header
 * configuration.
 */
const BACKEND_URL = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,

  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
    ];
  },

  // The archive is served as raw bytes by the backend, deliberately without a
  // thumbnail pipeline. Next's image optimiser would add a second cache that
  // could drift from the files on disk, so it stays off.
  images: {
    unoptimized: true,
  },

  eslint: {
    ignoreDuringBuilds: false,
  },
};

export default nextConfig;
