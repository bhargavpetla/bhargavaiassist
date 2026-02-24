FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install system dependencies (Node.js for WhatsApp bridge, build tools for C extensions)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg git \
        build-essential python3-dev \
        libxml2-dev libxslt1-dev zlib1g-dev && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy everything and install in one step
COPY . .

# Install Python dependencies
RUN uv pip install --system --no-cache .

# Build the WhatsApp bridge
WORKDIR /app/bridge
RUN npm install && npm run build
WORKDIR /app

# Create config directory
RUN mkdir -p /root/.nanobot

# Make start.sh executable
RUN chmod +x start.sh

# Web UI + health check port
EXPOSE 8080

CMD ["./start.sh"]
