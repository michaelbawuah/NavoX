FROM node:24-alpine AS base
WORKDIR /app

COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/ui/package.json packages/ui/package.json
COPY packages/assistant-runtime/package.json packages/assistant-runtime/package.json
RUN npm ci

COPY . .
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build --workspace=@navox/web

EXPOSE 3000
CMD ["npm", "run", "start", "--workspace=@navox/web"]
