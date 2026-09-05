import type { NextConfig } from "next";

/**
 * The browser talks to `/api/*` on its own origin and Next proxies through to the
 * backend. That keeps CORS out of the picture entirely and, more usefully, means
 * raw media and ZIP bundles are same-origin — so `<video>` seeking and
 * download-triggering anchors behave without preflight requests or exposed-header
 * configuration.
 *
 * Note that `rewrites()` is evaluated during `next build` and frozen into
 * routes-manifest.json, so this value is fixed at build time. The Dockerfile
 * therefore passes it as a build argument, not as a container environment
 * variable.
 */
const BACKEND_URL = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // Emits a self-contained server bundle with only the traced dependencies, so
  // the runtime image can skip node_modules entirely.
  output: "standalone",

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
