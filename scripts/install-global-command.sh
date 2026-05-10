#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit"
TARGET_BIN="/Users/michaelpierre/.local/bin"
TARGET_PATH="${TARGET_BIN}/toolkit"

mkdir -p "${TARGET_BIN}"

cat > "${TARGET_PATH}" <<'EOF'
#!/bin/zsh
set -euo pipefail

export VIDEO_CLI_TOOLKIT_ROOT="/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit"
exec "/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/.venv/bin/toolkit" "$@"
EOF

chmod +x "${TARGET_PATH}"

echo "Installed global toolkit launcher at ${TARGET_PATH}"
echo "Try: toolkit doctor"

