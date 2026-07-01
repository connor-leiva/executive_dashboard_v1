FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci || npm install
COPY . .
# Vite inlines VITE_* at BUILD time. On a Railway Dockerfile build, service
# variables are NOT exposed to build steps unless declared as ARG here — without
# this the app ships with an empty API base and silently falls back to sample
# data. Set VITE_API_BASE as a build variable on the `web` service.
ARG VITE_API_BASE
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM node:20-alpine
RUN npm i -g serve
WORKDIR /app
COPY --from=build /app/dist ./dist
EXPOSE 3000
# Bind Railway's injected $PORT (falls back to 3000 locally).
CMD ["sh", "-c", "serve -s dist -l ${PORT:-3000}"]
