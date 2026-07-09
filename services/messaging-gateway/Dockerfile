FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --omit=dev

COPY src ./src

ENV PORT=3107
EXPOSE 3107

CMD ["npm", "start"]
