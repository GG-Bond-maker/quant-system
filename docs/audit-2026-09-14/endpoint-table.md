| 端点 | 方法 | 鉴权 | HTTP | code | 结果 | 耗时 | 备注 |
|---|---|---|---|---|---|---|---|
| `/` | GET | public | 200 | 0 | ✅ 通过 | 11ms |  |
| `/api/v1/alerts/events` | GET | researcher | 200 | 0 | ✅ 通过 | 18ms |  |
| `/api/v1/alerts/events/read` | POST | viewer | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 6ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/alerts/health` | GET | researcher | 200 | 0 | ✅ 通过 | 6ms |  |
| `/api/v1/alerts/rules` | GET | researcher | 200 | 0 | ✅ 通过 | 16ms |  |
| `/api/v1/alerts/rules` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 7ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/alerts/rules/{rule_id}` | DELETE | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 6ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/alerts/rules/{rule_id}` | PUT | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 8ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/auth/login` | POST | public | 200 | 0 | ✅ 通过 | None | 正确口令 -> JWT 正常；错误口令 -> 40104；无效 token -> 40102；viewer 访问 researcher 端点 -> 40300 |
| `/api/v1/auth/me` | GET | any-auth | 200 | 0 | ✅ 通过 | 31ms |  |
| `/api/v1/auth/register` | POST | public | 200 | 0 | ✅ 通过 | None | 注册成功（已建测试账号 qaprobe_viewer，需清理）；重名 40105；弱口令 40000 |
| `/api/v1/auth/register/status` | GET | public | 200 | 0 | ✅ 通过 | 27ms |  |
| `/api/v1/backtest/run` | POST | researcher | 200 | 0 | ✅ 通过 | 702ms |  |
| `/api/v1/backtest/signal-analysis` | POST | researcher | 200 | 0 | ✅ 通过 | 1232ms |  |
| `/api/v1/backtest/strategy-run` | POST | researcher | 200 | 0 | ✅ 通过 | 3968ms |  |
| `/api/v1/datacenter/datasets` | GET | viewer | 200 | 0 | ✅ 通过 | 30ms |  |
| `/api/v1/datacenter/instruments` | GET | viewer | 200 | 0 | ✅ 通过 | 10ms |  |
| `/api/v1/datacenter/logs` | GET | researcher | 200 | 0 | ✅ 通过 | 54ms |  |
| `/api/v1/datacenter/mirror/rebuild` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 5ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/datacenter/mirror/status` | GET | viewer | 200 | 0 | ✅ 通过 | 49628ms | **慢** |
| `/api/v1/datacenter/overview` | GET | viewer | 200 | 0 | ✅ 通过 | 55544ms | **慢** |
| `/api/v1/datacenter/quality` | GET | viewer | 200 | 0 | ✅ 通过 | 14876ms | **慢** |
| `/api/v1/datacenter/sync` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 10ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/datacenter/sync/auto` | GET | researcher | 200 | 0 | ✅ 通过 | 10ms |  |
| `/api/v1/datacenter/sync/auto` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 26ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/datacenter/sync/cancel` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 31ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/datacenter/sync/fetch` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 8ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/datacenter/sync/status` | GET | researcher | 200 | 0 | ✅ 通过 | 9ms |  |
| `/api/v1/datacenter/sync/tasks/{task_id}` | GET | researcher | 200 | 51001 | ✅ 通过 | 15ms | 伪造 task_id，返回 51001 任务不存在（预期） |
| `/api/v1/datacenter/task-stats` | GET | viewer | 200 | 0 | ✅ 通过 | 48ms |  |
| `/api/v1/datacenter/text/build-factor` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 31ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/datacenter/text/import` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 23ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/datacenter/text/status` | GET | viewer | 200 | 0 | ✅ 通过 | 39ms |  |
| `/api/v1/datacenter/train/cancel` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 19ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/datacenter/train/readiness` | GET | public | 200 | 0 | ✅ 通过 | 585ms |  |
| `/api/v1/datacenter/train/start` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 7ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/datacenter/train/status` | GET | researcher | 200 | 0 | ✅ 通过 | 10ms |  |
| `/api/v1/desk/account` | GET | researcher | 200 | 0 | ✅ 通过 | 19ms |  |
| `/api/v1/desk/attribution` | POST | researcher | 200 | 0 | ✅ 通过 | 3204ms |  |
| `/api/v1/desk/capacity` | GET | researcher | 200 | 0 | ✅ 通过 | 50ms |  |
| `/api/v1/desk/exclusion` | GET | researcher | 200 | 0 | ✅ 通过 | 8ms |  |
| `/api/v1/desk/exclusion` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 6ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/desk/exclusion/screen` | GET | researcher | 200 | 0 | ✅ 通过 | 37ms |  |
| `/api/v1/desk/exclusion/toggle` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 6ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/desk/fills/run` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 6ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/desk/kill-switch` | GET | researcher | 200 | 0 | ✅ 通过 | 9ms |  |
| `/api/v1/desk/kill-switch` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 31ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/desk/orders` | GET | researcher | 200 | 0 | ✅ 通过 | 32ms |  |
| `/api/v1/desk/orders` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 5ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/etf/detail/{code}` | GET | viewer | 200 | 0 | ✅ 通过 | 40172ms | **慢** |
| `/api/v1/etf/flow` | GET | viewer | 200 | 0 | ✅ 通过 | 10ms |  |
| `/api/v1/etf/hot` | GET | viewer | 200 | 0 | ✅ 通过 | 6550ms |  |
| `/api/v1/etf/list` | GET | viewer | 200 | 0 | ✅ 通过 | 8310ms |  |
| `/api/v1/etf/overview` | GET | viewer | 200 | 0 | ✅ 通过 | 82831ms | **慢** |
| `/api/v1/etf/performance` | GET | viewer | 200 | 0 | ✅ 通过 | 80074ms | **慢** |
| `/api/v1/etf/scale` | GET | viewer | 200 | 0 | ✅ 通过 | 59748ms | **慢** |
| `/api/v1/export/backtest` | POST | researcher | 200 | None | ✅ 通过 | 902ms | 返回 xlsx 文件流（非 JSON，正常） |
| `/api/v1/export/screener` | GET | researcher | 200 | None | ✅ 通过 | 602ms | 返回 CSV 文件流（非 JSON，正常） |
| `/api/v1/export/strategy-backtest` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 21ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/market/index/kline` | GET | public | 200 | 0 | ✅ 通过 | 5914ms |  |
| `/api/v1/market/overview` | GET | public | 200 | 0 | ⚠️ 异常数据 | 34ms | stale=true；recommend/ai_stats=unavailable，anomalies=degraded 0 条 |
| `/api/v1/market/overview/daily` | GET | public | 200 | 0 | ✅ 通过 | 8ms |  |
| `/api/v1/market/overview/rt` | GET | public | 200 | 0 | ✅ 通过 | 9ms |  |
| `/api/v1/market/quotes` | GET | viewer | 200 | 0 | ✅ 通过 | 6570ms |  |
| `/api/v1/monitor/health` | GET | viewer | 200 | 0 | ✅ 通过 | 11ms |  |
| `/api/v1/monitor/run` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 29ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/notify/recent` | GET | viewer | 200 | 0 | ✅ 通过 | 30ms |  |
| `/api/v1/notify/stream` | GET | viewer | 200 | 0 | ⚠️ 异常 | 12000ms | SSE 已连接但 12s 内 0 字节，无心跳/初始事件 **慢** |
| `/api/v1/ops/dag` | GET | researcher | 200 | 0 | ✅ 通过 | 1899ms |  |
| `/api/v1/ops/dag/rerun` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 6ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/ops/lineage` | GET | researcher | 200 | 0 | ✅ 通过 | 26929ms | **慢** |
| `/api/v1/ops/quality-scan` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 6ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/portfolio/backtest` | POST | researcher | 200 | 51001 | ⚠️ 部分资产数据获取失败: 600519.SH: ValueError; 000001.SZ: ValueError | 84056ms | **慢** |
| `/api/v1/portfolio/search` | GET | viewer | 200 | 0 | ⚠️ 异常数据 | 175690ms | code=0 但返回 []，且耗时 175.7s **慢** |
| `/api/v1/report/daily` | GET | viewer | 200 | 0 | ✅ 通过 | 17ms |  |
| `/api/v1/report/daily/generate` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 29ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/research/cv-folds` | POST | researcher | 200 | 0 | ✅ 通过 | 20ms |  |
| `/api/v1/research/experiments` | GET | researcher | 200 | 0 | ✅ 通过 | 38ms |  |
| `/api/v1/research/factor-corr` | POST | researcher | 200 | 0 | ✅ 通过 | 1634ms |  |
| `/api/v1/research/factor-icir` | POST | researcher | 200 | 0 | ✅ 通过 | 1595ms |  |
| `/api/v1/research/factor-quantile` | POST | researcher | 200 | 0 | ✅ 通过 | 1411ms |  |
| `/api/v1/research/feature-importance` | GET | researcher | 200 | 0 | ✅ 通过 | 2017ms |  |
| `/api/v1/research/impact-sim` | POST | researcher | 200 | 0 | ✅ 通过 | 78ms |  |
| `/api/v1/research/lab/yearly` | GET | researcher | 200 | 0 | ✅ 通过 | 15821ms | **慢** |
| `/api/v1/research/optimize` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 27ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/research/overview` | GET | researcher | 200 | 0 | ✅ 通过 | 1047ms |  |
| `/api/v1/research/stress-test` | POST | researcher | 200 | 0 | ✅ 通过 | 1683ms |  |
| `/api/v1/screener` | GET | viewer | 200 | 0 | ⚠️ 异常数据 | 32ms | count=1 且 close/pct/amount 全 null（今日股票池快照未就绪，无 status 降级提示） |
| `/api/v1/screener/stocks` | GET | viewer | 200 | 0 | ✅ 通过 | 40ms |  |
| `/api/v1/screener/watchlist` | GET | viewer | 200 | 0 | ✅ 通过 | 40ms |  |
| `/api/v1/settings` | GET | viewer | 200 | 0 | ✅ 通过 | 10ms |  |
| `/api/v1/settings/apikeys/rotate` | POST | admin | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 5ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/settings/connectors/test` | POST | researcher | 200 | 0 | ✅ 通过 | 5240ms |  |
| `/api/v1/settings/data/cache/clear` | POST | admin | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 4ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/settings/data/sync` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 5ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/settings/db/backup` | POST | admin | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 19ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/settings/engine` | PUT | admin | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 30ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/settings/preferences` | PUT | viewer | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 26ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/stock/search` | GET | viewer | 200 | 0 | ✅ 通过 | 44ms |  |
| `/api/v1/stock/{symbol}/kline` | GET | viewer | 200 | 0 | ✅ 通过 | 42ms |  |
| `/api/v1/stock/{symbol}/panels` | GET | viewer | 200 | 0 | ✅ 通过 | 20002ms | **慢** |
| `/api/v1/stock/{symbol}/predict` | GET | researcher | 200 | 0 | ✅ 通过 | 10ms |  |
| `/api/v1/stock/{symbol}/profile` | GET | viewer | 200 | 0 | ✅ 通过 | 20ms |  |
| `/api/v1/studio/alpha-eval` | POST | researcher | 200 | 0 | ✅ 通过 | 4715ms |  |
| `/api/v1/studio/factor-report` | POST | researcher | 200 | 0 | ✅ 通过 | 11636ms | **慢** |
| `/api/v1/studio/factors` | GET | researcher | 200 | 0 | ✅ 通过 | 20ms |  |
| `/api/v1/studio/factors` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 27ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/studio/factors/{factor_id}` | DELETE | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 8ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/studio/mining/cancel/{task_id}` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 7ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/studio/mining/start` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 8ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/studio/mining/status/{task_id}` | GET | viewer | 200 | 51001 | ✅ 通过 | 10ms | 伪造 task_id，返回 51001 任务不存在（预期） |
| `/api/v1/studio/nl-to-factor` | POST | researcher | 200 | 40100 | 🔒 鉴权路径已验证（未执行写操作） | 21ms | 仅鉴权校验(未执行重型操作) |
| `/api/v1/watchlist/correlation` | GET | viewer | 200 | 0 | ✅ 通过 | 62ms |  |
| `/api/v1/watchlist/dashboard` | GET | viewer | 200 | 0 | ✅ 通过 | 13649ms | **慢** |
| `/docs` | GET | public | 200 | 0 | ✅ 通过 | None | Swagger UI 可访问 |
| `/docs/oauth2-redirect` | GET | public | - | - | ⛔未执行 | - | 未覆盖 |
| `/health` | GET | public | 200 | 0 | ✅ 通过 | 32ms |  |
| `/health/live` | GET | public | 200 | 0 | ✅ 通过 | 22ms |  |
| `/health/ready` | GET | public | 200 | 0 | ✅ 通过 | 35ms |  |
| `/metrics` | GET | public | 200 | None | ✅ 通过 | 6ms | Prometheus 文本格式（非 JSON，正常） |
| `/openapi.json` | GET | public | 200 | None | ✅ 通过 | 83ms | OpenAPI JSON（非业务信封，正常） |
| `/redoc` | GET | public | 200 | 0 | ✅ 通过 | None | ReDoc 可访问 |