FROM node:20-alpine

WORKDIR /workspace/frontend

COPY frontend/package.json /workspace/frontend/package.json
COPY frontend/package-lock.json /workspace/frontend/package-lock.json

RUN npm ci

COPY frontend /workspace/frontend

RUN npm run build

CMD ["sh", "-lc", "npm run build && npm run preview -- --host 0.0.0.0 --port 5173"]
