#!/bin/bash
#
# TODO: Replace with a job container that has msodbcsql18 pre-installed.
#
# Install the Microsoft ODBC driver for SQL Server.
#
# The FILE_MANIFEST from Microsoft is verified against a pinned checksum
# before it is trusted to verify the package repository configuration.
#
# Repurposed from https://github.com/Yarden-zamir/install-mssql-odbc
#
set -eu

VERSION_ID="$(awk -F= '$1=="VERSION_ID"{gsub(/"/, "", $2); print $2}' /etc/os-release)"
PKG_NAME="packages-microsoft-prod.deb"

# Pinned FILE_MANIFEST checksums per Ubuntu version.
case "${VERSION_ID}" in
  22.04) manifest_sha256="3fc171cfaeb40e8c2103267b28277c43de02269bd35ac7cb897b2bbd9a40f256" ;;
  24.04) manifest_sha256="52830b24a2c53300daa3991cb91997288e7440c3c66782ac003e4cf6c0271d04" ;;
  *)     printf "Unsupported Ubuntu version: %s\n" "${VERSION_ID}" >&2; exit 1 ;;
esac

FILE_MANIFEST_URL="https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/FILE_MANIFEST"
FILE_MANIFEST="hashes.txt"

curl -fsSL -o "${FILE_MANIFEST}" "${FILE_MANIFEST_URL}"
printf '%s  %s\n' "${manifest_sha256}" "${FILE_MANIFEST}" | sha256sum -c >/dev/null

expected_hash="$(awk -F, -v pkg="${PKG_NAME}" '$1==pkg {print $2}' "${FILE_MANIFEST}")"

curl -fsSL -O "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/${PKG_NAME}"
printf "%s *packages-microsoft-prod.deb\n" "${expected_hash}" | sha256sum -c -

# Install the Microsoft package repository configuration file using dpkg.
# The --force-confold option ensures that existing configuration files are not overwritten.
# The DEBIAN_FRONTEND=noninteractive environment variable suppresses interactive prompts.
sudo DEBIAN_FRONTEND=noninteractive dpkg --force-confold -i packages-microsoft-prod.deb

sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
