#!/bin/bash

set -e

# Prompt user for domain and email
read -p "Enter your domain or subdomain (e.g., api.eko360.ai): " DOMAIN
read -p "Enter your email address for Let's Encrypt notifications: " EMAIL

APP_NAME="llminer-backend"
NGINX_SITE="/etc/nginx/sites-available/$APP_NAME"
NGINX_SITE_LINK="/etc/nginx/sites-enabled/$APP_NAME"

echo "🔧 Updating Nginx config for $DOMAIN..."

# Update Nginx site configuration
sudo tee $NGINX_SITE > /dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# Enable and test Nginx config
sudo ln -sf $NGINX_SITE $NGINX_SITE_LINK
sudo nginx -t && sudo systemctl reload nginx

echo "✅ Nginx configured for $DOMAIN"

echo "📦 Installing Certbot and Nginx plugin..."
sudo apt update
sudo apt install -y certbot python3-certbot-nginx

echo "🔐 Requesting SSL certificate for $DOMAIN..."
sudo certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email "$EMAIL"

echo "✅ HTTPS certificate installed!"

# Test auto-renewal
echo "🔁 Testing certificate auto-renewal..."
sudo certbot renew --dry-run

echo "🎉 Success! You can now access your app at: https://$DOMAIN"
