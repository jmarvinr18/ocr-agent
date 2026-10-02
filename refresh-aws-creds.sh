#!/usr/bin/env bash
# Renew AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN in .env
# by assuming the role configured for an AWS CLI profile (with MFA).
#
# Usage:
#   ./refresh-aws-creds.sh [MFA_CODE]
#
# Env overrides:
#   AWS_PROFILE_NAME  profile in ~/.aws/config   (default: ai_developer_jmr)
#   DURATION          session length in seconds  (default: 3600)
#   ENV_FILE          file to update             (default: .env next to this script)

set -euo pipefail

PROFILE="${AWS_PROFILE_NAME:-ai_developer_jmr}"
DURATION="${DURATION:-3600}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "Env file not found: $ENV_FILE" >&2; exit 1; }

cfg() { aws configure get "$1" --profile "$PROFILE" 2>/dev/null | tr -d '\r' || true; }

ROLE_ARN="$(cfg role_arn)"
MFA_SERIAL="$(cfg mfa_serial)"
REGION="$(cfg region)"
BASE_KEY="$(cfg aws_access_key_id)"
BASE_SECRET="$(cfg aws_secret_access_key)"

[ -n "$ROLE_ARN" ]   || { echo "No role_arn for profile '$PROFILE'" >&2; exit 1; }
[ -n "$BASE_KEY" ] && [ -n "$BASE_SECRET" ] \
  || { echo "No long-term keys for profile '$PROFILE' in ~/.aws/credentials" >&2; exit 1; }

MFA_ARGS=()
if [ -n "$MFA_SERIAL" ]; then
  MFA_CODE="${1:-}"
  if [ -z "$MFA_CODE" ]; then
    read -r -p "MFA code for $MFA_SERIAL: " MFA_CODE
  fi
  MFA_ARGS=(--serial-number "$MFA_SERIAL" --token-code "$MFA_CODE")
fi

echo "Assuming $ROLE_ARN for ${DURATION}s ..."

# Call STS with the profile's long-term keys directly (not the profile itself,
# which would try to assume the role first). Clear any stale session vars.
# Note: don't launch aws via `env` - on Git Bash/Windows its stdout gets lost.
CREDS="$(
  unset AWS_PROFILE AWS_SESSION_TOKEN
  export AWS_ACCESS_KEY_ID="$BASE_KEY"
  export AWS_SECRET_ACCESS_KEY="$BASE_SECRET"
  export AWS_DEFAULT_REGION="${REGION:-ap-southeast-1}"
  export AWS_PAGER=""
  aws sts assume-role \
    --role-arn "$ROLE_ARN" \
    --role-session-name "${PROFILE}-$(date +%s)" \
    --duration-seconds "$DURATION" \
    "${MFA_ARGS[@]}" \
    --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken,Expiration]' \
    --output text | tr -d '\r'
)"

read -r NEW_KEY NEW_SECRET NEW_TOKEN EXPIRES <<<"$CREDS"
[ -n "${NEW_TOKEN:-}" ] || { echo "Failed to get credentials from STS" >&2; exit 1; }

# Replace (or append) keys in the env file. awk avoids sed issues with '/' and '+'.
set_var() {
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$val" '
    BEGIN { done = 0 }
    $0 ~ "^"k"=" { print k"="v; done = 1; next }
    { print }
    END { if (!done) print k"="v }
  ' "$ENV_FILE" > "$tmp"
  mv "$tmp" "$ENV_FILE"
}

cp "$ENV_FILE" "$ENV_FILE.bak"
set_var AWS_ACCESS_KEY_ID     "$NEW_KEY"
set_var AWS_SECRET_ACCESS_KEY "$NEW_SECRET"
set_var AWS_SESSION_TOKEN     "$NEW_TOKEN"

echo "Updated $ENV_FILE (backup: $ENV_FILE.bak)"
echo "Credentials expire at: $EXPIRES"
