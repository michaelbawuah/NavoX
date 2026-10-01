FROM node:24-bookworm-slim
WORKDIR /app

# Temporal's native core bridge requires glibc. Keep the assistant worker on
# its own TypeScript image; the Web image may remain Alpine.
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/ui/package.json packages/ui/package.json
COPY packages/assistant-runtime/package.json packages/assistant-runtime/package.json
RUN npm ci

COPY . .
CMD ["npm", "run", "worker:goals", "--workspace=@navox/assistant-runtime"]
