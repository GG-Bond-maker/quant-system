#!/bin/sh
# 验证 pre-commit 钩子的**双闸门控制流**：
#   ① 两道都通过 → exit 0
#   ② 第二道失败 → 仍然 exit 1（证明不会因第一道通过就提前 exit 0）
#
# 通过改写 HOOK 里的 repo_root 检测来在无 git 元数据的仓库里运行。

ROOT="D:/Python_Project/Alpha Quant Platform"
HOOK="$ROOT/.githooks/pre-commit"

# 造一个"能定位仓库根目录"的版本
sed "s#^repo_root=.*#repo_root=\"$ROOT\"#" "$HOOK" \
  | sed 's#^} *$#}#' > /tmp/hook_probe.sh

# 去掉 git 检测那段的失败分支（把 `|| { ...; exit 0; }` 整块换成空操作）
awk '
  /^repo_root=/ { print "repo_root=\"" ENVIRON["ROOTDIR"] "\""; skip=1; next }
  skip==1 && /^}/ { skip=0; next }
  skip==1 { next }
  { print }
' ROOTDIR="$ROOT" "$HOOK" > /tmp/hook_probe.sh

chmod +x /tmp/hook_probe.sh
sh -n /tmp/hook_probe.sh || { echo "语法错误"; exit 2; }

echo "=== 场景 ① 两道闸门均通过 ==="
sh /tmp/hook_probe.sh; rc1=$?
echo "exit=$rc1  $([ $rc1 -eq 0 ] && echo '正确（放行）' || echo '异常（不该拦截）')"
echo

echo "=== 场景 ② 破坏导航入口后（第二道闸门应失败）==="
cp "$ROOT/frontend/src/components/Sidebar.tsx" /tmp/sidebar.probe.bak
sed -i "/{ to: '\/etf', label: 'ETF 中心'/d" "$ROOT/frontend/src/components/Sidebar.tsx"
sed -i "/{ to: \`\/etf\/\${DEFAULT_ETF_CODE}\`, label: 'ETF 分析'/d" "$ROOT/frontend/src/components/Sidebar.tsx"
sh /tmp/hook_probe.sh; rc2=$?
echo "exit=$rc2  $([ $rc2 -ne 0 ] && echo '正确（已拦截）' || echo '异常（未拦截！）')"
cp /tmp/sidebar.probe.bak "$ROOT/frontend/src/components/Sidebar.tsx"

echo
echo "=== 恢复后复检 ==="
sh /tmp/hook_probe.sh; rc3=$?
echo "exit=$rc3  $([ $rc3 -eq 0 ] && echo '正确（放行）' || echo '异常')"

echo
if [ $rc1 -eq 0 ] && [ $rc2 -ne 0 ] && [ $rc3 -eq 0 ]; then
  echo "控制流验证通过：两道闸门串联、任一失败即阻断。"
else
  echo "控制流验证失败。"
  exit 1
fi
