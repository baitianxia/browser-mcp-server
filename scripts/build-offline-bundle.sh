#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

PROFILE=core
OUTPUT_DIR="$PROJECT_ROOT/dist"
NODE_DISTRIBUTION=""
TARGET=native
FORCE=0

usage() {
  echo "Usage: $0 [--profile core|diagnostic] [--target native|windows-x64] [--output-dir DIR] [--node-distribution DIR] [--force]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE=${2:?missing value for --profile}
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR=${2:?missing value for --output-dir}
      shift 2
      ;;
    --node-distribution)
      NODE_DISTRIBUTION=${2:?missing value for --node-distribution}
      shift 2
      ;;
    --target)
      TARGET=${2:?missing value for --target}
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$PROFILE" != "core" && "$PROFILE" != "diagnostic" ]]; then
  echo "--profile must be core or diagnostic" >&2
  exit 2
fi
if [[ "$TARGET" != "native" && "$TARGET" != "windows-x64" ]]; then
  echo "--target must be native or windows-x64" >&2
  exit 2
fi

NODE_BIN=${NODE_BIN:-$(command -v node || true)}
PNPM_BIN=${PNPM_BIN:-$(command -v pnpm || true)}
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3 || true)}

if [[ -z "$NODE_BIN" || ! -x "$NODE_BIN" ]]; then
  echo "Set NODE_BIN to Node.js 20.19+" >&2
  exit 2
fi
if [[ -z "$PNPM_BIN" || ! -x "$PNPM_BIN" ]]; then
  echo "Set PNPM_BIN to pnpm 11.19.0" >&2
  exit 2
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Set PYTHON_BIN to Python 3.10+" >&2
  exit 2
fi
"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
  echo "Python 3.10+ is required" >&2
  exit 2
}

NODE_VERSION=$("$NODE_BIN" --version)
PNPM_VERSION=$("$PNPM_BIN" --version)
if [[ "$PNPM_VERSION" != "11.19.0" ]]; then
  echo "pnpm 11.19.0 is required; found $PNPM_VERSION" >&2
  exit 2
fi
"$NODE_BIN" -e 'const v=process.versions.node.split(".").map(Number); if (v[0] < 20 || (v[0] === 20 && v[1] < 19)) process.exit(1)' || {
  echo "Node.js 20.19+ is required; found $NODE_VERSION" >&2
  exit 2
}

HOST_SYSTEM=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$HOST_SYSTEM" in
  darwin|linux) ;;
  *)
    echo "Unsupported build host: $HOST_SYSTEM; use build-offline-bundle.ps1 on Windows" >&2
    exit 2
    ;;
esac
HOST_MACHINE=$(uname -m | tr '[:upper:]' '[:lower:]')
case "$HOST_MACHINE" in
  x86_64|amd64) HOST_MACHINE=x64 ;;
  arm64|aarch64) HOST_MACHINE=arm64 ;;
  *)
    echo "Unsupported build architecture: $HOST_MACHINE" >&2
    exit 2
    ;;
esac

TARGET_SYSTEM=$HOST_SYSTEM
TARGET_MACHINE=$HOST_MACHINE
CROSS_BUILT=0
if [[ "$TARGET" == "windows-x64" ]]; then
  TARGET_SYSTEM=windows
  TARGET_MACHINE=x64
  CROSS_BUILT=1
  if [[ "$PROFILE" != "core" ]]; then
    echo "Cross-built Windows candidates are limited to the core profile" >&2
    exit 2
  fi
  if [[ -z "$NODE_DISTRIBUTION" ]]; then
    echo "Windows one-click bundles require --node-distribution" >&2
    exit 2
  fi
fi

export PYTHONDONTWRITEBYTECODE=1

ARTIFACT_NAME="browser-agent-runtime-1.0.10-${PROFILE}-${TARGET_SYSTEM}-${TARGET_MACHINE}"
mkdir -p "$OUTPUT_DIR"
ARCHIVE="$OUTPUT_DIR/$ARTIFACT_NAME.tar.gz"

if [[ -e "$ARCHIVE" || -e "$ARCHIVE.sha256" ]]; then
  if [[ "$FORCE" -ne 1 ]]; then
    echo "Refusing to overwrite $ARCHIVE; pass --force" >&2
    exit 2
  fi
fi

BUILD_TEMP=$(mktemp -d "$OUTPUT_DIR/.browser-agent-build.XXXXXX")
cleanup() {
  rm -rf -- "$BUILD_TEMP"
}
trap cleanup EXIT

STAGE="$BUILD_TEMP/$ARTIFACT_NAME"
mkdir -p "$STAGE"
cp "$PROJECT_ROOT/runtime/package.json" "$STAGE/package.json"
cp "$PROJECT_ROOT/runtime/pnpm-lock.yaml" "$STAGE/pnpm-lock.yaml"
cp "$PROJECT_ROOT/runtime/pnpm-workspace.yaml" "$STAGE/pnpm-workspace.yaml"
mkdir -p "$STAGE/bin"
if [[ "$TARGET_SYSTEM" == "windows" ]]; then
  cp "$PROJECT_ROOT/runtime/bin/playwright-mcp.cmd" "$STAGE/bin/playwright-mcp.cmd"
  if [[ "$PROFILE" == "diagnostic" ]]; then
    cp "$PROJECT_ROOT/runtime/bin/chrome-devtools-mcp.cmd" "$STAGE/bin/chrome-devtools-mcp.cmd"
  fi
else
  cp "$PROJECT_ROOT/runtime/bin/playwright-mcp" "$STAGE/bin/playwright-mcp"
  chmod 0755 "$STAGE/bin/playwright-mcp"
  if [[ "$PROFILE" == "diagnostic" ]]; then
    cp "$PROJECT_ROOT/runtime/bin/chrome-devtools-mcp" "$STAGE/bin/chrome-devtools-mcp"
    chmod 0755 "$STAGE/bin/chrome-devtools-mcp"
  fi
fi

export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
export PNPM_DISABLE_SELF_UPDATE_CHECK=1
export NO_UPDATE_NOTIFIER=1
INSTALL_ARGS=(--dir "$STAGE" install --prod --frozen-lockfile --ignore-scripts)
"$PNPM_BIN" "${INSTALL_ARGS[@]}"

"$PYTHON_BIN" "$SCRIPT_DIR/prepare_runtime_tree.py" \
  --node-modules "$STAGE/node_modules" \
  --profile "$PROFILE"

"$PYTHON_BIN" "$SCRIPT_DIR/check_runtime_portability.py" \
  --root "$STAGE/node_modules" \
  --target-os "$TARGET_SYSTEM" \
  --target-arch "$TARGET_MACHINE"

RUNTIME_NODE="$NODE_BIN"
BUNDLED_NODE_ARG=""
if [[ -n "$NODE_DISTRIBUTION" ]]; then
  if [[ "$TARGET_SYSTEM" == "windows" ]]; then
    "$PYTHON_BIN" "$SCRIPT_DIR/validate_node_distribution.py" \
      "$NODE_DISTRIBUTION" \
      --expected-version "$NODE_VERSION" \
      --approval-file "$PROJECT_ROOT/config/windows-node-sources.json"
    cp -R "$NODE_DISTRIBUTION" "$STAGE/node"
    "$PYTHON_BIN" "$SCRIPT_DIR/validate_node_distribution.py" \
      "$STAGE/node" \
      --expected-version "$NODE_VERSION" \
      --approval-file "$PROJECT_ROOT/config/windows-node-sources.json"
    if [[ "$CROSS_BUILT" -eq 0 ]]; then
      RUNTIME_NODE="$STAGE/node/node.exe"
    fi
  else
    if [[ ! -x "$NODE_DISTRIBUTION/bin/node" ]]; then
      echo "--node-distribution must contain executable bin/node" >&2
      exit 2
    fi
    cp -R "$NODE_DISTRIBUTION" "$STAGE/node"
    RUNTIME_NODE="$STAGE/node/bin/node"
  fi
  BUNDLED_NODE_ARG=--bundled-node
fi

"$RUNTIME_NODE" "$STAGE/node_modules/@playwright/mcp/cli.js" --help >/dev/null
if [[ "$PROFILE" == "diagnostic" ]]; then
  "$RUNTIME_NODE" \
    "$STAGE/node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools-mcp.js" \
    --help >/dev/null
elif [[ -e "$STAGE/node_modules/chrome-devtools-mcp/package.json" ]]; then
  echo "Core profile unexpectedly contains chrome-devtools-mcp" >&2
  exit 1
fi

"$PYTHON_BIN" "$SCRIPT_DIR/generate_sbom.py" \
  --node-modules "$STAGE/node_modules" \
  --output "$STAGE/SBOM.cdx.json"

METADATA_ARGS=(
  --output "$STAGE/BUILD-METADATA.json"
  --profile "$PROFILE"
  --node-version "$NODE_VERSION"
  --pnpm-version "$PNPM_VERSION"
  --target-system "$TARGET_SYSTEM"
  --target-machine "$TARGET_MACHINE"
)
if [[ -n "$BUNDLED_NODE_ARG" ]]; then
  METADATA_ARGS+=("$BUNDLED_NODE_ARG")
fi
if [[ "$CROSS_BUILT" -eq 1 ]]; then
  METADATA_ARGS+=(--cross-built)
fi
"$PYTHON_BIN" "$SCRIPT_DIR/write_build_metadata.py" "${METADATA_ARGS[@]}"

INTEGRITY_ARGS=(--root "$STAGE")
ARCHIVE_ARGS=(--root "$STAGE")
if [[ "$TARGET_SYSTEM" == "windows" ]]; then
  INTEGRITY_ARGS+=(--reject-links)
  ARCHIVE_ARGS+=(--reject-links)
fi
"$PYTHON_BIN" "$SCRIPT_DIR/write_integrity.py" "${INTEGRITY_ARGS[@]}"

STAGED_ARCHIVE="$BUILD_TEMP/$ARTIFACT_NAME.tar.gz"
"$PYTHON_BIN" "$SCRIPT_DIR/create_bundle_archive.py" \
  "${ARCHIVE_ARGS[@]}" \
  --output "$STAGED_ARCHIVE"
"$PYTHON_BIN" "$SCRIPT_DIR/write_archive_hash.py" "$STAGED_ARCHIVE"
"$PYTHON_BIN" "$SCRIPT_DIR/verify-bundle.py" "$STAGED_ARCHIVE"
mv -f "$STAGED_ARCHIVE" "$ARCHIVE"
mv -f "$STAGED_ARCHIVE.sha256" "$ARCHIVE.sha256"

echo "Built $ARCHIVE"
echo "Checksum $ARCHIVE.sha256"
