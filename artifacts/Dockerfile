FROM node:22-alpine
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY server.mjs ./
COPY vendor ./vendor
EXPOSE 8080
CMD ["node", "server.mjs"]
