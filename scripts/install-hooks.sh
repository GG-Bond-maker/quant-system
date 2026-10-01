#!/bin/sh
# 启用仓库自带的 git hooks（当前只有 pre-commit：前端风格闸门）。
#
# 为什么需要它：git 不会自动读取 `.githooks/`，必须显式配置 `core.hooksPath`
# （该配置是**本地**的，不随仓库分发）——这是本方案唯一的「一次性手工步骤」，
# 故用脚本固化，避免每台机器靠记忆。
#
# 用法：  sh scripts/install-hooks.sh
# 取消：  git config --unset core.hooksPath
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

git config core.hooksPath .githooks

# 可执行位：Windows 上由 git 用自带 sh 执行，位通常不影响；
# 但在 macOS / Linux 上 clone 后必须可执行，故显式补上（失败不致命）。
chmod +x .githooks/pre-commit 2>/dev/null || true

echo "已启用 git hooks：core.hooksPath = $(git config core.hooksPath)"
echo "验证：git config core.hooksPath  （应输出 .githooks）"
