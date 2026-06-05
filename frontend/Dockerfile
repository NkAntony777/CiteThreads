# CiteThreads frontend — multi-stage build.
#
# Stage 1 (build): compile the Vite app into static assets.
# Stage 2 (runtime): serve the assets with nginx + SPA fallback so
#                    client-side routes (e.g. /projects/abc) work.
#
# Build:    docker build -t citethreads-frontend frontend/
# Run:      docker run --rm -p 8080:80 citethreads-frontend
FROM node:20-alpine AS build

WORKDIR /app

# Install deps in their own layer so source changes don't invalidate
# the cache. The package-lock.json is the authoritative source of
# versions, so we copy it first.
COPY package.json package-lock.json ./
RUN npm ci

# Now copy the source and build.
COPY . .

# `npm run build` runs `tsc -b && vite build`. Type errors fail the
# build, which is the behavior we want in CI.
RUN npm run build

# ---------------------------------------------------------------------------
# Runtime: nginx
# ---------------------------------------------------------------------------
FROM nginx:alpine AS runtime

# Replace the default site config with one that does SPA fallback
# (unknown paths serve index.html so React Router can take over).
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static assets are produced in /app/dist during the build stage.
COPY --from=build /app/dist /usr/share/nginx/html

# Run as the unprivileged nginx user (already configured by the base
# image). We don't bind to a port below 1024.
EXPOSE 80

# Default CMD inherited from nginx:alpine (`nginx -g "daemon off;"`).
