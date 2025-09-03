#!/usr/bin/env bash

set -xve
#Repurposed from https://github.com/Yarden-zamir/install-mssql-odbc

VERSION_ID="$(awk -F= '$1=="VERSION_ID"{gsub(/"/, "", $2); print $2}' /etc/os-release)"
PKG_NAME="packages-microsoft-prod.deb"

FILE_MANIFEST_URL="https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/FILE_MANIFEST"
FILE_MANIFEST="hashes.txt"

curl -sSL -o "${FILE_MANIFEST}" "${FILE_MANIFEST_URL}"

expected_hash=$(grep "${PKG_NAME}" "${FILE_MANIFEST}" | cut -d',' -f2)

curl -sSL -O "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/${PKG_NAME}"
printf "%s *packages-microsoft-prod.deb\n" "${expected_hash}" | sha256sum -c -

# Install the Microsoft package repository configuration file using dpkg.
# The `--force-confold` option ensures that existing configuration files are not overwritten
# The `DEBIAN_FRONTEND=noninteractive` environment variable suppresses interactive prompts
sudo DEBIAN_FRONTEND=noninteractive dpkg --force-confold -i packages-microsoft-prod.deb
#rm packages-microsoft-prod.deb

sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
