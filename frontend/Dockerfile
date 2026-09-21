# =========================================================================
# POLARIS frontend - production image
#
# Stage 1 builds the React bundle with Vite; stage 2 serves the static
# output from nginx. The runtime image contains no Node, no source and no
# build tooling.
#
# VITE_API_URL must be supplied at BUILD time, not run time: Vite inlines it
# into the bundle.
#
#   docker build --build-arg VITE_API_URL=https://api.example.com/api .
# =========================================================================

# ---------- stage 1: build ----------
FROM node:20-alpine AS build

WORKDIR /app

# Copy manifests first so `npm ci` is cached until dependencies change.
COPY package*.json ./
RUN npm ci

COPY . .

ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build


# ---------- stage 2: serve ----------
FROM nginx:1.27-alpine AS runtime

RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
