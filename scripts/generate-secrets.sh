#!/usr/bin/env bash
# ============================================================================
# generate-secrets.sh — generate strong production secrets for Project Seraphim
# ============================================================================
# Generates a JWT secret, DB password, webhook secret, and other secrets, then
# writes a ready-to-use .env.production.
#
# If .env.production.template exists, it is used as the base and every
# "__GENERATE_ME__" placeholder is substituted with a freshly generated value.
# Otherwise the generated values are simply printed for you to copy.
#
# This script REFUSES to overwrite an existing .env.production unless you pass
# --force, so you cannot accidentally rotate live secrets.
#
# Usage:
#   chmod +x scripts/generate-secrets.sh      # one-time: make it executable
#   ./scripts/generate-secrets.sh             # write ./.env.production
#   ./scripts/generate-secrets.sh --force     # overwrite an existing file
#   ./scripts/generate-secrets.sh --print     # only print secrets, write nothing
#   ./scripts/generate-secrets.sh -o /path/.env.production   # custom output path
#
# Requires: openssl, sed (POSIX). Run from anywhere — paths are resolved
# relative to the repo root (the parent of this scripts/ directory).
# ============================================================================
set -eu

# --- Resolve repo paths (works regardless of the caller's CWD) --------------
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

TEMPLATE_FILE="$REPO_ROOT/.env.production.template"
OUTPUT_FILE="$REPO_ROOT/.env.production"
FORCE=0
PRINT_ONLY=0

# --- Parse args -------------------------------------------------------------
while [ "$#" -gt 0 ]; do
    case "$1" in
        --force|-f)
            FORCE=1
            ;;
        --print|-p)
            PRINT_ONLY=1
            ;;
        -o|--output)
            shift
            [ "$#" -gt 0 ] || { echo "ERROR: $0: -o/--output requires a path argument" >&2; exit 2; }
            OUTPUT_FILE="$1"
            ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Try: $0 --help" >&2
            exit 2
            ;;
    esac
    shift
done

# --- Preconditions ----------------------------------------------------------
if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: 'openssl' is required but was not found in PATH." >&2
    exit 1
fi

# --- Secret generators ------------------------------------------------------
# JWT/webhook secret: 64 hex chars (256 bits). Comfortably > the 32-char min.
gen_hex() {
    openssl rand -hex 32
}

# DB password: base64 of 24 random bytes, stripped of characters that are
# awkward inside a postgresql:// URL (+ / =), then trimmed to 32 chars.
# Still ~190 bits of entropy before trimming — plenty.
gen_db_password() {
    openssl rand -base64 24 | tr -d '+/=' | cut -c1-32
}

JWT_SECRET=$(gen_hex)
WEBHOOK_SECRET=$(gen_hex)
DB_PASSWORD=$(gen_db_password)
GITEA_DB_PASSWORD=$(gen_db_password)

# --- Print-only mode --------------------------------------------------------
print_secrets() {
    echo "JWT_SECRET=$JWT_SECRET"
    echo "WEBHOOK_SECRET=$WEBHOOK_SECRET"
    echo "DB_PASSWORD=$DB_PASSWORD"
    echo "GITEA_DB_PASSWORD=$GITEA_DB_PASSWORD"
}

if [ "$PRINT_ONLY" -eq 1 ]; then
    echo "# Generated secrets (NOT written to disk). Copy into your env file:"
    print_secrets
    exit 0
fi

# --- Refuse to clobber an existing file unless --force ----------------------
if [ -e "$OUTPUT_FILE" ] && [ "$FORCE" -ne 1 ]; then
    echo "ERROR: $OUTPUT_FILE already exists." >&2
    echo "       Refusing to overwrite existing secrets. Re-run with --force to replace it." >&2
    exit 1
fi

# --- Helper: substitute one placeholder for a literal value in the output ---
# Uses a non-/ sed delimiter (|) and escapes & and | in the replacement so
# generated secrets never corrupt the sed expression.
substitute() {
    key="$1"
    value="$2"
    esc=$(printf '%s' "$value" | sed -e 's/[&|]/\\&/g')
    # Replace KEY=__GENERATE_ME__ with KEY=<value> (KEY anchored at line start).
    sed "s|^${key}=__GENERATE_ME__|${key}=${esc}|" "$OUTPUT_FILE" > "$OUTPUT_FILE.tmp"
    mv "$OUTPUT_FILE.tmp" "$OUTPUT_FILE"
}

if [ -f "$TEMPLATE_FILE" ]; then
    # Start from the template, then fill in each generated secret.
    cp "$TEMPLATE_FILE" "$OUTPUT_FILE"
    # Restrict permissions before writing secrets into the file.
    chmod 600 "$OUTPUT_FILE" 2>/dev/null || true

    substitute "JWT_SECRET" "$JWT_SECRET"
    substitute "WEBHOOK_SECRET" "$WEBHOOK_SECRET"
    substitute "DB_PASSWORD" "$DB_PASSWORD"
    substitute "GITEA_DB_PASSWORD" "$GITEA_DB_PASSWORD"

    echo "Wrote $OUTPUT_FILE (from .env.production.template) with generated secrets."
    echo ""
    echo "Remaining __GENERATE_ME__ placeholders still need real values"
    echo "(external service credentials you must supply yourself):"
    # List any placeholders we did not auto-fill so the owner knows what's left.
    # Match only "KEY=__GENERATE_ME__" assignment lines (skip comment text).
    grep -nE '^[A-Za-z_][A-Za-z0-9_]*=__GENERATE_ME__' "$OUTPUT_FILE" \
        || echo "  (none — all placeholders filled)"
    echo ""
    echo "NEXT STEPS:"
    echo "  1. Fill in the remaining __GENERATE_ME__ values above (OAuth, CompreFace, CiviCRM)."
    echo "  2. Set DOMAIN=... if you use the Caddy reverse proxy."
    echo "  3. Keep this file OUT of git. NOTE: .gitignore only ignores '.env',"
    echo "     not '.env.production' — either rename it to '.env' next to the"
    echo "     compose file, or add '.env.production' to .gitignore before committing."
else
    echo "WARNING: $TEMPLATE_FILE not found — printing generated secrets instead." >&2
    echo "# Copy these into your production env file:"
    print_secrets
fi
