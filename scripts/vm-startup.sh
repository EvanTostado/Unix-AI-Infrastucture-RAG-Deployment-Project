#!/bin/bash

set -euo pipefail

#log into the startup process
exec > >(tee -a /var/log/linux-ai-startup.log) 2>&1

echo "Starting Linux AI VM setup..."

#Update packages and install Docker
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io


# Start Docker and enable it on boot
systemctl enable --now docker

#verify Docker is running
docker --version
systemctl is-active docker

echo "Linux AI VM setup complete!"
