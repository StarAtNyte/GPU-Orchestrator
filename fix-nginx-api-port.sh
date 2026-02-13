#!/bin/bash
sed -i 's|proxy_pass http://localhost:8080/api/;|proxy_pass http://localhost:8810/api/;|' /etc/nginx/includes/gpu-polling.conf
nginx -t && nginx -s reload && echo "Done"
