@echo off
REM ===========================================================================
REM AQP 下游重建启动器（2026-10-02）
REM
REM 背景：20:47 启动的 `--steps rebuild` 在 21:18:12 被 WorkBuddyAI 应用重启
REM       连带杀掉（qfq 只完成 3425/5223，日志与 err 日志双双冻结）。
REM       本启动器把重定向放进 cmd，使 python 进程脱离 agent shell 的进程树，
REM       降低再次被应用重启连带终止的概率。
REM
REM 幂等性：rebuild_qfq 由本地 raw+hfq 全量重算 qfq（无网络），可安全重跑；
REM         后续 build_universe / build_universe_bt / build_features / infer /
REM         screener_dump 亦为幂等离线步骤。中断后重复执行本脚本即可续跑。
REM
REM 用法：双击本文件，或  Start-Process -FilePath run_rebuild2.cmd -WindowStyle Hidden
REM ===========================================================================
setlocal
set "ROOT=D:\Python_Project\Alpha Quant Platform"
set "PY=%ROOT%\backend\.venv\Scripts\python.exe"
set "SCRIPT=%ROOT%\scripts\expand_universe_2500.py"
set "LOG=%ROOT%\expand_2022_rebuild2.log"
set "ELOG=%ROOT%\expand_2022_rebuild2.err.log"

cd /d "%ROOT%\backend"
echo [%DATE% %TIME%] launcher start > "%LOG%"
"%PY%" "%SCRIPT%" --steps rebuild,report ^
  --universe-name universe_full.json ^
  --state-name .expand_2022r3_state.json ^
  --report-name expand_full_report.json >> "%LOG%" 2>> "%ELOG%"
echo [%DATE% %TIME%] launcher exit rc=%ERRORLEVEL% >> "%LOG%"
endlocal
