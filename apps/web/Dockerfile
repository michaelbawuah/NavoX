FROM node:24-alpine AS base
WORKDIR /app

COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/ui/package.json packages/ui/package.json
RUN npm ci

COPY . .
RUN npm run build --workspace=@navox/web

EXPOSE 3000
CMD ["npm", "run", "start", "--workspace=@navox/web"]

