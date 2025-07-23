#!/bin/bash
set -e  # Exit on any error

# Update package list and install Nginx and Certbot
echo "📦 Updating package list and installing necessary packages..."
sudo apt update -y
sudo apt install -y nginx certbot python3-certbot-nginx

# Allow HTTPS traffic through the firewall
echo "🛡️ Allowing HTTPS traffic through UFW firewall..."
sudo ufw allow 'Nginx Full'

# Get user input for IP and domain
echo "🔧 Setting up Nginx with SSL for your server..."
read -p "Enter your server's IP address [51.20.115.13]: " IP
IP=${IP:-51.20.115.13}

read -p "Enter your domain name [www.genaigov.ai]: " DOMAIN
DOMAIN=${DOMAIN:-api.genaigov.ai}

echo "✅ IP set to: $IP"
echo "✅ Domain set to: $DOMAIN"

# DNS verification
echo
echo "🔍 Checking if DNS A records are correctly set..."
echo "Expected A Records:"
echo "@    -> $IP"
echo "www  -> $IP"

# Check root domain
echo "🔍 Checking DNS for $DOMAIN..."
RESULT=$(nslookup $DOMAIN | grep 'Address:' | tail -n1 | awk '{print $2}')
if [ "$RESULT" != "$IP" ]; then
  echo "❌ $DOMAIN is not pointing to $IP. Found: $RESULT"
else
  echo "✅ $DOMAIN correctly points to $IP"
fi

# Check www subdomain
echo "🔍 Checking DNS for www.$DOMAIN..."
RESULT_WWW=$(nslookup www.$DOMAIN | grep 'Address:' | tail -n1 | awk '{print $2}')
if [ "$RESULT_WWW" != "$IP" ]; then
  echo "❌ www.$DOMAIN is not pointing to $IP. Found: $RESULT_WWW"
else
  echo "✅ www.$DOMAIN correctly points to $IP"
fi

# Stop Nginx before standalone certbot
echo "🛑 Stopping Nginx before SSL request..."
sudo systemctl stop nginx

# Backup existing Nginx config
echo "💾 Backing up existing Nginx config..."
sudo cp /etc/nginx/sites-available/default /etc/nginx/sites-available/default.bak

# Request certificate
echo "🔐 Requesting SSL certificate for $DOMAIN..."
sudo certbot certonly --standalone -d $DOMAIN

# Reconfigure Nginx
NGINX_CONFIG="/etc/nginx/sites-available/default"
echo "📝 Writing Nginx config to $NGINX_CONFIG..."
sudo bash -c "cat > $NGINX_CONFIG" <<EOF
server {
    listen 80;
    server_name $IP $DOMAIN;

    # Redirect HTTP to HTTPS
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name $IP $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'HIGH:!aNULL:!MD5';

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# Test config
echo "🧪 Testing Nginx configuration..."
if ! sudo nginx -t; then
    echo "❌ Nginx config test failed. Aborting."
    exit 1
fi

# Reload and start Nginx
echo "🔄 Reloading Nginx..."
sudo systemctl reload nginx

echo "🚀 Starting Nginx..."
sudo systemctl start nginx

echo "✅ Done! Nginx is running and HTTPS is set up for $DOMAIN"

