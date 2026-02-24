#!/usr/bin/env bash
set -euo pipefail

# ── Build ~/.nanobot/config.json from environment variables ──
CONFIG_DIR="$HOME/.nanobot"
CONFIG_FILE="$CONFIG_DIR/config.json"
mkdir -p "$CONFIG_DIR"

# Model config
MODEL="${NANOBOT_MODEL:-custom/moonshotai/kimi-k2.5}"

# Provider keys
NVIDIA_KEY="${NVIDIA_API_KEY:-}"
NVIDIA_BASE="${NVIDIA_BASE_URL:-https://integrate.api.nvidia.com/v1}"
OPENAI_KEY="${OPENAI_API_KEY:-}"

# At least one provider must be configured
if [ -z "$NVIDIA_KEY" ] && [ -z "$OPENAI_KEY" ]; then
  echo "ERROR: Set NVIDIA_API_KEY or OPENAI_API_KEY"
  exit 1
fi

echo "INFO: Model=$MODEL"
if [ -n "$NVIDIA_KEY" ]; then echo "INFO: NVIDIA API key is set"; fi
if [ -n "$OPENAI_KEY" ]; then echo "INFO: OpenAI API key is set"; fi

# Telegram config
TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TG_ALLOW="${TELEGRAM_ALLOW_FROM:-*}"
TG_ENABLED="${TELEGRAM_ENABLED:-false}"

# Web search
BRAVE_KEY="${BRAVE_API_KEY:-}"

# Build config JSON
cat > "$CONFIG_FILE" <<CONF
{
  "agents": {
    "defaults": {
      "model": "$MODEL",
      "maxTokens": 4096,
      "temperature": 0.7
    }
  },
  "providers": {
    "custom": {
      "apiKey": "$NVIDIA_KEY",
      "apiBase": "$NVIDIA_BASE"
    },
    "openai": {
      "apiKey": "$OPENAI_KEY"
    }
  },
  "channels": {
    "telegram": {
      "enabled": $TG_ENABLED,
      "token": "$TG_TOKEN",
      "allowFrom": ["$TG_ALLOW"]
    }
  },
  "tools": {
    "web": {
      "search": {
        "apiKey": "$BRAVE_KEY",
        "maxResults": 5
      }
    }
  }
}
CONF

echo "INFO: Config written to $CONFIG_FILE"

# ── Keep-alive pinger (Render free tier spins down after 15min) ──
PORT="${PORT:-8080}"
(
  sleep 30
  while true; do
    curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1 || true
    sleep 600
  done
) &

echo "INFO: Starting nanobot gateway..."
exec nanobot gateway
