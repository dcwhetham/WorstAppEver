# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# /web — Next.js dashboard.
#
# Build context is this directory (the frontend needs nothing from the rest of
# the repo; it reaches the backend over HTTP only).
# ---------------------------------------------------------------------------

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Next freezes rewrites into routes-manifest.json during the build, so the proxy
# target has to be supplied here rather than at container start. The default is
# the compose service name.
ARG BACKEND_INTERNAL_URL=http://backend:8000
ENV BACKEND_INTERNAL_URL=$BACKEND_INTERNAL_URL

# Lint and type errors fail the build rather than shipping. This is the control
# surface for a data-integrity tool, so a silently broken render is worse than
# no deploy at all.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -g 10002 nodejs && adduser -u 10002 -G nodejs -S nextjs

# `output: "standalone"` traces the dependency graph and copies only what the
# server actually imports, which is why there is no node_modules here.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
