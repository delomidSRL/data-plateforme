#!/bin/sh
set -e

# Vite bakes VITE_API_BASE_URL at build time, which would defeat "one versioned image,
# deployed anywhere" — this writes the real value at container start instead, and
# src/api/client.js reads window.__API_BASE_URL__ first.
cat > /usr/share/nginx/html/config.js <<EOF
window.__API_BASE_URL__ = "${API_BASE_URL:-}";
EOF
