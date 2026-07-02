#!/usr/bin/env bash
# 管线平台一键安装脚本
set -euo pipefail

REPO_URL="${PIPELINE_REPO_URL:-https://github.com/sophiezel/pipeline-platform.git}"
INSTALL_DIR="${PIPELINE_INSTALL_DIR:-$HOME/.local/share/pipeline-platform}"
BIN_DIR="${HOME}/.local/bin"

log() { printf '  %s\n' "$*"; }
die() { printf '❌ %s\n' "$*" >&2; exit 1; }

# Resolve project root: run from repo, or clone into INSTALL_DIR
if [[ -f "${BASH_SOURCE[0]:-}" ]] && [[ -f "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pyproject.toml" ]]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  log "正在克隆 pipeline-platform → ${INSTALL_DIR}"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" pull --ff-only
  else
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
  ROOT="$INSTALL_DIR"
fi

# Python >= 3.10
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done
[[ -n "$PYTHON" ]] || die "需要 Python 3.10+，请先安装: https://www.python.org/downloads/"

VENV="${ROOT}/.venv"
log "创建虚拟环境: ${VENV}"
"$PYTHON" -m venv "$VENV"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

log "安装 pipeline CLI"
pip install -q --upgrade pip
pip install -q -e "${ROOT}"

log "准备 skill 目录"
mkdir -p "${HOME}/.pi/skills" "${HOME}/.pi/agent/skills" "${HOME}/.agents/skills"

log "链接命令到 ${BIN_DIR}/pipeline"
mkdir -p "$BIN_DIR"
ln -sf "${VENV}/bin/pipeline" "${BIN_DIR}/pipeline"

if ! command -v pipeline >/dev/null 2>&1; then
  case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *)
      log "提示: 将 ${BIN_DIR} 加入 PATH（可写入 ~/.zshrc 或 ~/.bashrc）"
      log "  export PATH=\"\${HOME}/.local/bin:\${PATH}\""
      ;;
  esac
fi

VERSION="$(pipeline --version 2>/dev/null || true)"
printf '\n✅ 安装完成'
[[ -n "$VERSION" ]] && printf ' — %s' "$VERSION"
printf '\n   运行: pipeline doctor\n\n'
