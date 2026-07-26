# React Router 8 SSR app (Node server). Build target: linux/amd64.
FROM node:24-alpine AS build
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm run build
# Drop dev-only deps in place so the runtime image stays lean (build already ran).
RUN pnpm prune --prod

FROM node:24-alpine
RUN corepack enable
WORKDIR /app
ENV NODE_ENV=production PORT=3000
COPY --from=build /app/package.json /app/pnpm-lock.yaml ./
COPY --from=build /app/node_modules /app/node_modules
COPY --from=build /app/build /app/build
EXPOSE 3000
CMD ["pnpm", "run", "start"]
