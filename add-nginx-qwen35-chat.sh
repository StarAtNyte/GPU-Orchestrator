#!/bin/bash
# Adds the qwen35-chat nginx location block and reloads nginx

CONF="/etc/nginx/includes/gpu-polling.conf"

# Check if already added
if grep -q "qwen35-chat" "$CONF"; then
    echo "qwen35-chat entry already exists in $CONF"
    exit 0
fi

sudo tee -a "$CONF" > /dev/null << 'EOF'

# Qwen3.5 Chat UI
location /gpu-polling/qwen35-chat {
    return 301 /gpu-polling/qwen35-chat/;
}

location /gpu-polling/qwen35-chat/ {
    proxy_pass http://localhost:8867/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_cache_bypass $http_upgrade;
    proxy_read_timeout 300s;
}
EOF

sudo nginx -t && sudo systemctl reload nginx && echo "Done — nginx reloaded"
