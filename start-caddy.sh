#!/bin/sh
set -e

echo "Generating Caddyfile from template..."
echo "HOST_IP: $HOST_IP"
echo "HTTPS_EMAIL: $HTTPS_EMAIL"

# Copy template to Caddyfile
cp /etc/caddy/Caddyfile.template /etc/caddy/Caddyfile

# Substitute HOST_IP
sed -i "s/\${HOST_IP}/$HOST_IP/g" /etc/caddy/Caddyfile

# Handle TLS configuration
if [ -z "$HTTPS_EMAIL" ]; then
    echo "HTTPS_EMAIL not set, using internal TLS"
    # Keep tls internal (already in template)
else
    echo "HTTPS_EMAIL is set: $HTTPS_EMAIL"
    # Replace tls internal (with newline) with tls $HTTPS_EMAIL
    sed -i "s/tls internal/tls $HTTPS_EMAIL/" /etc/caddy/Caddyfile
fi

echo "Generated Caddyfile:"
cat /etc/caddy/Caddyfile

# Run Caddy
echo "Starting Caddy..."
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile