# 残留项修复（第三轮）· 来源 IP 归因 + 跨端点业务码统一

> 上游：`residual-fixes-2026-10-04.md`（第二轮「动手」3 项 + 新发现第 4 项）。
> 本轮按用户裁定执行 **① 业务码统一 + ② 来源 IP 归因修复 + ③ IP 维度限速（与 ② 打包）**。
> ⚠️ 改动**仍未提交**，需人工 review 后 commit。

---

## 一、① 跨端点业务码统一（`ml/predict.py`）

**问题**：`/api/v1/stock/{symbol}/predict` 在"无生产模型 / 产物缺失"时返回 `ERR_INFER(52001)`，
而 `api/v1/research.py::_resolve_production_lgbm` 对**同一个问题**（从注册表取生产模型，取不到）
返回 `ERR_DATA_EMPTY(51001)`。

**为什么这是缺陷而不是风格差异**：两个端点回答同一个问题却给两个码 ⇒ "前端按码分类"必然在
其中一个上失效。实测前端只有 `pages/FactorStudio/index.tsx:96` 按 `51001` 分类，**`52001` 全仓
无任何消费方** —— 也就是说按现状写分支的代码，落在 `/predict` 上就会静默漏判。语义上
`51001`（本地无数据/产物）也比 `52001`（推理失败）更准：这里推理根本没开始。

**改法**：`except (FileNotFoundError, ModelRegistryError)` → `AQPException(ERR_DATA_EMPTY, str(e))`，
与 research 同口径；`ERR_INFER` 在本文件已无其它用处，从导入中移除。

**证据**：`tests/test_model_registry.py::test_predict_symbol_maps_missing_production_model_to_business_code`
断言 `code == ERR_DATA_EMPTY`。

---

## 二、② 来源 IP 归因修复（`core/auth.py` + `core/config.py`）

**问题（真缺陷）**：`client_ip()` 取 `X-Forwarded-For` 的**首跳**当来源 IP；而
`frontend/nginx.conf:102` 用的是 `$proxy_add_x_forwarded_for`，其语义是
`"$http_x_forwarded_for, $remote_addr"` —— **追加**，不是覆写。⇒ **首跳恰好就是客户端
自己塞进去的值**。两个后果：

* 审计日志（OWASP A09 留痕）可被**投毒**成任意 IP；
* 若拿它做限速键，**一个请求头即可绕过**（"已防护"的错觉比不做更糟）。

**改法**：新增 `client_ip_info(request) -> (ip, attributable)`，规则：

| 直连对端 | 行为 | attributable |
|---|---|---|
| 是**已声明的**受信代理（`TRUSTED_PROXY_IPS`） | 优先 `X-Real-IP`（nginx 用 `$remote_addr` **覆写**，客户端改不了）；退回 XFF **尾跳**（追加语义下尾栈才是真实对端） | True |
| 是受信代理但无可解析的头 | 只剩代理自身地址 | **False** |
| 不是受信代理 | 代理头**一律不采信**，只用 socket 对端 | True |

外加两道加固：
* **值必须校验为 IP 字面量**（`ipaddress.ip_address`）—— 这些值会进审计日志，头里塞
  `"1.2.3.4\n[FAKE] INFO login ok"` 就能**伪造整行日志**（日志注入）；校验后可注入字符集被压到
  `0-9a-f:.`。
* **写错的 CIDR 必须启动期喊出来**：`parse_trusted_proxies` 把非法项**原样返回**而不是静默丢弃
  （静默丢弃 = "配了但没生效"，正是本项目反复出现的形态）；prod fail-fast、dev 告警，沿用
  `required_prod_safe` 既有语义。

`client_ip()` 保留原签名（4 处日志调用点零改动），改为委托 `client_ip_info()[0]`。

**默认值**：`TRUSTED_PROXY_IPS=""` ⇒ 不采信任何代理头。**部署拓扑未知时不猜**。

---

## 三、③ 登录限速的「来源 IP」维度（`api/v1/auth.py`）

**问题**：`_check_rate_limit(username, ip)` 只按 **username** 计数（`ip` 仅进日志）⇒ 单账号桶
拦不住"换用户名喷洒"（每个用户名只失败 1~2 次，永远到不了 10 次阈值）。

**改法**：新增**独立的** IP 桶（`_login_attempts_by_ip`，键单独存放避免与用户名撞键），
阈值 `_MAX_IP_ATTEMPTS = 30 / 5min`（= 单账号 3×：一个出口 IP 后可能有多个用户，NAT 容忍；
但 30 次失败/5min 仍明确是异常流量）。

⚠️ **双重门槛，缺一不可**（这是本项最容易做错的地方）：

1. **IP 必须可归因**（`attributable=True`）—— 否则攻击者换个请求头就能换桶；
2. **运维必须显式打开 `LOGIN_IP_RATE_LIMIT`**（默认 `false`）—— IP 归因依赖部署拓扑：
   反代未声明、或出口有 NAT 汇聚时，按 IP 限速会把多个用户并进**同一个桶**（**自伤，
   比不做更糟**）。

另有一处刻意的非对称：**成功登录只清账号桶，不清 IP 桶** —— 否则攻击者只要有一个合法
账号，就能用一次成功登录洗白自己 IP 上的全部攻击痕迹。

**部署说明**（这是 ③ 唯一需要人工做的事）：

```dotenv
# 反代部署（nginx 与 API 同机）
TRUSTED_PROXY_IPS=127.0.0.1
LOGIN_IP_RATE_LIMIT=true
# 直连部署（无反向代理）
LOGIN_IP_RATE_LIMIT=true
```

> 打开 `LOGIN_IP_RATE_LIMIT` 但未声明 `TRUSTED_PROXY_IPS` 时，启动会打一条告警提醒
> "若前面有反代，所有用户会共享同一个桶"。

**未改动**：注册限速仍只按 username（`ALLOW_REGISTRATION=false` 时该入口本就不开放），
本轮不动以控制范围。

---

## 四、验收证据

| 项 | 结果 |
|---|---|
| 新增用例 | `tests/test_client_ip_attribution.py`（**18 例**：5 组归因契约 + 5 组双重门槛/桶语义） |
| 相关回归 | `test_auth / test_p3_security / test_security_headers / test_model_registry / test_runtime_safety / test_client_ip_attribution` = **75 passed** |
| **证伪** | 注入"缺陷态"（`predict.ERR_DATA_EMPTY→52001`、`client_ip_info`/`client_ip`→旧首跳实现，**两处绑定** `core.auth` + `api.v1.auth`）⇒ **11 failed / 35 passed**（46 = 18 + 15 + 13）。**实测集合 == 预测集合**（判据 5/37）。⚠️ 早前一轮曾出现"预测 11、实测 10"，差异**已解释**：当时未把 `test_auth.py::test_login_ip_bucket_ignores_forged_forwarded_for` 纳入运行范围，且 `test_untrusted_peer_ignores_proxy_headers[headers1]`（只带伪造 `X-Real-IP`）在**旧实现下恰好也通过**（旧实现只读 XFF、根本不看 `X-Real-IP`）—— 属预测偏差，非测试问题（该例仍守"未受信对端不得采信 `X-Real-IP`"契约）。补齐运行范围后 11/11 全部命中 |
| ruff（CI 口径 `--select F,E9`） | **All checks passed** |
| mypy | **2 errors**，均为 `research.py:669` 既有基线（本轮无新增） |
| 后端全量 | **2254 passed / 8 skipped / 4 deselected / 0 failed**（`pytest tests -m "not network"`，`PYTEST_EXIT=0`，592s）—— 见文末"六、后端全量"，含一次**验证缺口**的发现与补跑 |

**证伪为何用「活模块 dict」而不是改生产文件**（判据 4/60）：脚本 `$TEMP/aqp_falsify_ipcode_2026-10-04.py`
只替换**内存中的模块对象**，不落盘、不留副本，宿主重复执行也无副作用。

---

## 五、仍然不动（沿用上一轮结论）

| 项 | 结论 |
|---|---|
| 计算池队列无界 | **不做**：实测取消传播（`$TEMP/aqp_probe_pool_cancel_2026-10-04.py`）证明排队任务会被真正取消、队列自排空；加有界队列若复用同一信号量会死锁 |
| `data/etf.py` 的 `js.eval` | **不做**：执行的是 pinned 依赖里的常量字符串、非用户输入 ⇒ 不可达；移植有静默错误风险 |
| 错误消息透传含路径 | **不做**：与既有先例 `research.py:352` 同款且有测试钉住；要做须两端一起改（保留 `生产模型文件缺失` 前缀） |

---

## 六、后端全量

```
pytest tests -m "not network" -q -p no:cacheprovider
=== 2254 passed, 8 skipped, 4 deselected, 0 failed in 592.37s ===
PYTEST_EXIT=0
```

对比本轮改动前基线 **2235 passed** ⇒ **+19**，与新增用例数**逐项吻合**：
`test_client_ip_attribution.py`（+18）、`test_auth.py::test_login_ip_bucket_ignores_forged_forwarded_for`（+1）。

### ⚠️ 一次真实的验证缺口（本轮自查发现）

第一次全量跑出 **2253 passed / 0 failed**，数字看着没问题；但**逐文件比对点号数**发现
`tests\test_auth.py` 只 **12** 点，而 `pytest tests/test_auth.py --collect-only -q` 实测
**13 tests collected**。查 `stat`：该文件 mtime = **20:24:53**，晚于该轮 pytest 的**收集时刻**
（启动 20:22:53、收集 ≈ 20:23:12）⇒ **最后一次编辑发生在收集之后**，那一轮跑的是旧版本，
**少算 1 例**。逐文件核 mtime 确认：晚于启动时刻的**只有这一个文件**（其余后端生产/测试文件
均早于 20:20:37），即缺口只在 `test_auth.py`、且只是**测试文件**（生产代码全部被那一轮覆盖）。

补跑（`$TEMP/aqp_full_verify3_2026-10-04.log`）⇒ **2254 passed / 0 failed**，
点号数复核：`test_auth.py` = **13**、`test_client_ip_attribution.py` = **18**。

> **教训（已沉淀为判据 75）**：`passed` 数 / `0 failed` / `PYTEST_EXIT=0` 三者**都不能**回答
> "跑的是不是当前树"。验收后必须把 **逐文件 mtime** 与 **该轮收集时刻** 对齐 ——
> 这是判据 22「文件存在不能判进度」的同型，只不过这次被冒充的是"测试通过"。

**尾部 `SystemExit: 1` 说明**：全量日志末尾有一段 `sitecustomize.py::_check_bulk_delete_guard`
的栈，来自 `pytest_unconfigure` 在**汇总行之后**清理临时目录时被宿主 safe-delete 守卫拦下
（与本项目前一轮同源）。它发生在 `=== ... ===` 之后，不影响结果：`PYTEST_EXIT=0`、
汇总行 0 failed。**勿据此判失败**。
