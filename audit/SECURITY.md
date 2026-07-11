# market_monitor 安全审计报告（Pass A）

> 审计日期：2026-07-10 · 审计范围：`D:\market_monitor` 仓库全部代码 + 完整 git 历史 + 已部署依赖清单
> 性质：**只读审计**。本次会话未修改任何项目文件，仅在 `audit/` 下写报告、在 `audit/raw/` 下存工具原始输出。
> 读者：项目所有者（财务背景）。工程术语首次出现时紧跟一句话说明。

---

## a. 总体结论：最坏情况下会发生什么

**一句话：现在唯一"已经发生"的安全事故，是你有一把 Dune Analytics 的 API 密钥（API key = 一串字符，代表"我是你"，谁拿到谁就能以你的身份调用那个服务、花你的额度）躺在一个公开的 GitHub 仓库的历史记录里，任何人都能翻出来。除此之外，没有发现任何"现在就能被陌生人从公网打进来"的漏洞。**

展开说最坏情况：

1. **已经泄露的东西（P0）**：2026-04-03 的第一个提交里有个 `test.py`，第 14 行硬编码了一把 Dune API key。这个文件后来被删了，但**删文件不等于删历史**——git 会永久保存每一次提交的内容。你的仓库 `github.com/mashiro1988/market_monitor` 经确认是 **PUBLIC（公开）**。任何人 `git clone` 之后翻一下历史就能拿到这把 key，用你的额度跑查询。好消息是我核对过：你本地 `.env` 里现在用的 Dune key 和泄露的那把**不是同一把**（指纹不同），说明你大概率换过。但**换 key ≠ 吊销旧 key**——旧的那把在 Dune 后台没被删掉之前，一直有效。

2. **被公开的"靶子情报"（P1）**：你以为不会上传的三份个人笔记（`ARCHITECTURE.md` / `DATAFLOW.md` / `DECISIONS.md`）其实**已经上传到公开仓库了**。它们里面写着你另一台服务器的地址和登录方式：`root@47.243.252.92`。这不是密码，但等于告诉全世界"这台机器开着 root 直接登录"。攻击者会拿它去撞密码。

3. **一条需要先攻陷上游、才能打到你的路径（P1）**：程序会读一种叫 `.pkl` 的数据文件（pickle = Python 的一种"把内存对象存成文件"的格式）。`pickle` 有个致命特性：**读取它等于执行它里面的代码**。这些 `.pkl` 来自另一台机器（BMAC 数据中心）或同机另一个程序的输出目录。如果那个上游被人控制、把 `.pkl` 换成恶意文件，你的 market_monitor 进程会以 `ubuntu` 用户身份执行攻击者的任意命令。需要先攻陷上游，所以不是"现在就能被利用"。

4. **不是问题的问题**：本项目**不碰钱**——不下单、不转账、不记账，只拉行情、存快照、推微信通知。所以"用浮点数算钱导致精度错误"这类财务风险在这里**不存在**。价格用浮点数只是用来显示和跟阈值比大小，误差量级远小于行情本身的跳动。

5. **依赖漏洞看着吓人，实际打不到你**：`npm audit`（前端依赖体检）报了 1 个 critical、3 个 high。我逐个查了 `package-lock.json`：**除 `react-router` 外全部标记 `dev: true`**，意思是它们只在你本机开发/跑测试时用，**根本不会进入部署到服务器上的产物**。唯一真正上线的 `react-router` 那条漏洞需要"程序拿用户给的地址做跳转"，而你全站只有一处跳转且目标写死（`/market`）。所以这一批：真实风险接近零，属于卫生问题。

6. **审计期间发生的意外（P1-4，需要你处理）**：本次审计跑扫描工具时，把原始输出写进了 `audit/raw/`。在我工作期间，你（或另一个会话）做了两次提交（22:26 和 22:36）并推送到了公开 GitHub，**把我的扫描产物一并扫了进去**。其中 `server-pip-freeze.txt` 现在公开列出了你服务器上 76 个包的精确版本号——等于把"我这台机器装的是哪些有已知漏洞的版本"直接贴给了攻击者。好消息：`gitleaks.json` 里的密钥值是 `REDACTED`（我扫描时加了脱敏参数），**没有二次泄露密钥**。

**如果什么都不做的最坏结局**：陌生人用你泄露的 Dune key 刷爆你的 Dune 免费额度/账单；同时拿 `47.243.252.92` 这个地址（P1-1）配合公开的精确依赖版本清单（P1-4）去针对性地找攻击面。目前**没有**发现能直接接管 market_monitor 服务本身的路径。

---

## b. 分级表

| 级别 | 定义 | 本次发现 |
|---|---|---|
| **P0**（现在就可被利用 / 密钥已泄露） | 1 条 | [P0-1] Dune API key 硬编码在公开仓库 git 历史中 |
| **P1**（一步之遥） | 4 条 | [P1-1] 公开仓库暴露 SFTP 服务器 `root@47.243.252.92`（且笔记文件是"以为没传但传了"）<br>[P1-2] `pickle.load` 反序列化外部数据 = 潜在任意代码执行<br>[P1-3] `APP_AUTH_TOKEN` 为空时应用层鉴权**静默关闭**，全站只剩 Nginx 一道门<br>[P1-4] **审计期间**本次扫描产物被并发提交并推送到公开仓库，泄露服务器精确依赖版本清单 |
| **P2**（卫生问题） | 8 条 | [P2-1] Linux 上残留扫描锁永远清不掉 → 监控可能永久停摆<br>[P2-2] 新闻源异常被静默吞掉 → 数据悄悄丢失、监控失明<br>[P2-3] 后端依赖已知漏洞（pip-audit，均不可达）<br>[P2-4] 前端依赖已知漏洞（npm audit，均为 dev-only 或不可达）<br>[P2-5] `hmac.compare_digest` 遇非 ASCII 请求头抛异常 → 500 而非 401<br>[P2-6] 企业微信 webhook 预览"不泄密"靠字符串长度巧合，非设计保证<br>[P2-7] `REMOTE_ALLOW_UNKNOWN_HOST=1` 会关闭 SSH 主机指纹校验（默认安全）<br>[P2-8] 无任何速率限制，昂贵端点可被反复调用 |
| **误报**（工具报了但不是问题） | 3 条 | 见 §d，含 bandit 的 1 条 High |

---

## c. 逐条发现

### P0-1 · Dune API key 硬编码在公开仓库的 git 历史中

**位置**：`test.py:14`（提交 `75ab339`，2026-04-03「initial commit」；该文件已在提交 `a15fb17`「chore: remove dead code and legacy modules」中删除，但**历史中永久留存**）

**证据**：

1. gitleaks 扫描 240 个提交，命中 1 条（原始输出：`audit/raw/gitleaks.json`）：
```json
{
  "RuleID": "generic-api-key",
  "File": "test.py",  "StartLine": 14,
  "Commit": "75ab33987b00e8ee68e59afe103dbd532d7b2db0",
  "Date": "2026-04-03T05:15:04Z",  "Entropy": 4.625,
  "Link": "https://github.com/mashiro1988/market_monitor/blob/75ab339.../test.py#L14"
}
```
2. 该文件上下文确认这是 Dune Analytics 的 key（`git show 75ab339:test.py`，第 14 行为 `api_key = "<32 位密钥>"`，紧接着第 21 行 `headers = {"X-DUNE-API-KEY": api_key}`，请求 `https://api.dune.com/api/v1/...`）。
3. 仓库确认为**公开**（`gh repo view mashiro1988/market_monitor --json visibility` → `{"isPrivate":false,"visibility":"PUBLIC"}`）。
4. 我在**不打印密钥明文**的前提下比对了指纹：泄露值的 SHA-256 前缀 `1C851D9BA3099EEC`，本地 `.env` 中现用 `DUNE_API_KEY` 的 SHA-256 前缀 `8F8CE29572DF5639`，两者**不同**，长度同为 32。

**人话解释影响**：
API key 就像一张写着你名字的门禁卡。你把这张卡的照片贴在了一个所有人都能看的公告栏上（公开仓库），后来你把公告撕了（删掉 test.py），但公告栏保留了所有历史照片（git 历史）。任何人翻一下就能拿到卡号，然后以你的名义去 Dune 刷查询、耗你的额度或账单。
指纹比对说明你**换了新卡**，但**旧卡只要没在 Dune 后台注销，就还能开门**。

另需知道：GitHub 上公开过的密钥会被爬虫在几分钟内抓走并索引。所以"改写 git 历史把它抹掉"只是清理现场，**吊销旧 key 才是止血**。

**修复建议**（按顺序）：
1. **立刻**：登录 Dune 后台 → API keys → 删除/revoke 那把旧 key。这一步做完，风险即归零。
2. 顺手确认 Dune 账单/用量有无异常调用。
3. 可选（清理现场，非止血）：用 `git filter-repo` 从历史中抹掉 `test.py`，然后 force push。注意这会重写所有提交哈希，且**对已被爬走的内容无效**——所以第 1 步做了之后，这步优先级很低。
4. `config.py:474` 的 `DUNE_API_KEY` 目前是休眠配置（`onchain_data/dune_queries.py` 未被 app / API 加载，已 grep 确认）。如果不再用 Dune，直接把这几行和 `onchain_data/` 一起删掉最干净——留到 Pass C 死代码清理时一并处理。

**验证方法**：
```bash
# 1) 确认吊销后，旧 key 已不在 Dune 控制台列表中（人工，浏览器）
# 2) 确认仓库当前工作区无密钥残留：
gitleaks detect --source . --redact       # 期望：no leaks found（历史仍会报，除非重写）
# 3) 确认 app 运行时不再依赖 Dune：
grep -rn "dune" --include=*.py api/ services/ scanners/ alerts/ run.py   # 期望：无输出
```

---

### P1-1 · 公开仓库暴露 SFTP 服务器 `root@47.243.252.92`（且"以为没上传"的笔记其实上传了）

**位置**：
- `services/remote_fs.py:3`（模块 docstring）
- `docs/remote_data_format.md:3`
- `docs/specs/remote_data_integration.md:12`、`:123`（`REMOTE_HOST=47.243.252.92`）
- `docs/specs/deployment.md:76`、`:98`、`:276`
- **以及三份你标记为"本地自用、不入库"的文件**：`ARCHITECTURE.md:21`、`DATAFLOW.md:282`、`DECISIONS.md:196`、`ARCHITECTURE.html:51`

**证据**：

`.gitignore:57-64` 明确写着这几个文件不该入库：
```
# ===== 个人工作笔记 / 项目地图 (本地自用，不入库) =====
AGENTS.md
ARCHITECTURE.md
ARCHITECTURE.html
DATAFLOW.md
DATAFLOW.html
DECISIONS.md
```
但它们**实际上都在版本控制里**：
```
$ git ls-files ARCHITECTURE.md DATAFLOW.md DECISIONS.md ARCHITECTURE.html DATAFLOW.html
ARCHITECTURE.html
ARCHITECTURE.md
DATAFLOW.html
DATAFLOW.md
DECISIONS.md
```
原因：**`.gitignore` 只对"尚未被追踪"的文件生效**。这些文件在被写进 `.gitignore` 之前就已经 `git add` 过了，之后每次 `git commit -a` 都继续带上它们。最近一次推送时间 `pushedAt: 2026-07-10T14:26:39Z`。

内容示例（`DATAFLOW.md:282`）：
> `root@47.243.252.92:/root/data_center/data/`。拉取失败保留上次缓存……

我也确认了**明文密码从未进入 git**：全历史搜索 `REMOTE_PASSWORD=<有值>` 只匹配到文档里的空值 `REMOTE_PASSWORD=  # 切公钥后清空`；`.env` 从未被提交（`git log --all -- .env` 无输出）。`docs/specs/deployment.md:98` 提到的"那条泄露过的明文密码"**在 git 里查无实据**，泄露渠道应在仓库之外（例如聊天记录）。

**人话解释影响**：
两件事：
1. **你以为是私人笔记的东西是公开的**。这本身可能就不符合你的预期——那三个文件里有你的架构决策、服务器路径、数据源细节。
2. 公开的内容里包含 `root@47.243.252.92`，等于告诉全网："这个 IP 上有台服务器，用 root 账号登 SSH"。攻击者的自动化脚本会拿这个 IP 去撞常见密码。这不是密钥泄露（P0），但省掉了攻击者的侦察步骤——所以是"一步之遥"（P1）。

**修复建议**：
1. 决定这三份笔记是否要公开。若不要：`git rm --cached ARCHITECTURE.md ARCHITECTURE.html DATAFLOW.md DATAFLOW.html DECISIONS.md` 然后提交（`--cached` = 只从版本控制移除，**本地文件保留**）。注意：历史里仍有，同 P0-1 第 3 条的取舍。
2. `47.243.252.92` 这台服务器：禁用 root 密码登录（`PermitRootLogin prohibit-password`）、只用密钥、装 `fail2ban`。`docs/specs/deployment.md:277` 已经把这条写成建议，落实即可。
3. 按 `docs/specs/deployment.md:76` 的方案 B，生产实例已改为 `REMOTE_BACKEND=local`（读同机文件，不连 SFTP）。若确认不再需要跨境 SFTP，把 `47.243.252.92` 的相关凭据全部作废。

**验证方法**：
```bash
git ls-files ARCHITECTURE.md DATAFLOW.md DECISIONS.md    # 期望：修复后无输出
# 服务器侧确认 root 密码登录已禁用：
ssh mmon "sudo sshd -T | grep -iE 'permitrootlogin|passwordauthentication'"
```

---

### P1-2 · `pickle.load` 反序列化外部数据 = 潜在任意代码执行

**位置**：
- 反序列化点：`services/remote_fs.py:491-494`
- 调用方：`scanners/sector_scanner.py:108`、`services/sector_service.py:68`

**证据**：

```python
# services/remote_fs.py:491
def load_pickle(local_path: Path):
    ...
    return pickle.load(f)          # :494
```
```python
# scanners/sector_scanner.py:108   /   services/sector_service.py:68
obj = remote_fs.load_pickle(path)
```
bandit 独立报出同一处（原始输出 `audit/raw/bandit.txt`）：
> `[B301:blacklist] Pickle ... can be unsafe when used to deserialize untrusted data`
> Severity: Medium Confidence: High · Location: `.\services\remote_fs.py:494:15`

这些 `.pkl` 的来源（`docs/specs/deployment.md:75-76`）：SFTP 模式下来自 `root@47.243.252.92`；`local` 模式下来自同机另一个程序 `coin-realtime-data` 的输出目录 `/home/ubuntu/data/firm/coin-realtime-data_v1.1.11/data/`。

**人话解释影响**：
`pickle` 是 Python 把内存里的表格存成文件的一种格式。它的设计里有个众所周知的坑：**读取一个 pickle 文件的过程，等于执行文件里夹带的任意 Python 代码**。它不是"解析数据"，而是"照着文件里的指令重建对象"，而指令可以是"运行这条系统命令"。

所以信任边界（trust boundary = 你信任谁提供的数据）在这里是：你的 market_monitor 完全信任那台 BMAC 服务器 / 同机那个 co-tenant 程序。如果它们中任何一个被入侵、把 `.pkl` 换成恶意文件，你的进程就会以 `ubuntu` 用户身份执行攻击者的命令——读你的 `.env`（里面有 DeepSeek key、CMC key、企业微信 webhook）、装后门，都做得到。

**为什么是 P1 而不是 P0**：攻击者必须**先**控制上游那台机器或那个目录。他不能从公网直接触发。所以是"一步之遥"，不是"现在就能被利用"。

**修复建议**（按性价比排序）：
1. **最便宜**：确认那个目录只有 root 可写、`ubuntu` 只读。若 co-tenant 程序以 root 跑、产物 `644 root:root`（`deployment.md:75` 声称如此），那么"能改这些文件的人"已经是 root，风险大幅下降。**先验证这一点，可能就不需要改代码。**
2. 中期：如果这些 `.pkl` 里只是 pandas DataFrame（纯数据），换成 `parquet` 格式（`df.to_parquet` / `pd.read_parquet`）。parquet 只存数据不存代码，读它不会执行任何东西。这是根治。
3. 不推荐：写"安全 unpickler"白名单——容易漏，收益不如换格式。

**验证方法**：
```bash
# 1) 确认 pkl 目录的属主与权限（期望 root 拥有、组/其他用户无 w 位）
ssh mmon "ls -ln /home/ubuntu/data/firm/coin-realtime-data_v1.1.11/data/preprocess_1h_resample/30m/ | head"
ssh mmon "stat -c '%U %G %a' /home/ubuntu/data/firm/coin-realtime-data_v1.1.11/data/preprocess_1h_resample/30m"
# 2) 确认生产用的是 local 后端（不连那台 SFTP）
ssh mmon "grep '^REMOTE_BACKEND=' /opt/market_monitor/.env"     # 期望 local
```

---

### P1-3 · `APP_AUTH_TOKEN` 为空时，应用层鉴权静默关闭

**位置**：`api/app.py:316-334`（关键行 `:318`、`:323`、`:329`）

**证据**：

```python
# api/app.py:317
async def optional_api_auth(request: Request, call_next):
    token = config.APP_AUTH_TOKEN.strip()          # :318
    path = request.url.path
    if (
        token                                       # ← token 为空字符串时，整个 if 不成立
        and path.startswith("/api/")
        and path != "/api/health"                   # :323
        and request.method != "OPTIONS"
    ):
        ...
        if not hmac.compare_digest(supplied, token):    # :329
            return JSONResponse(status_code=401, ...)
    return await call_next(request)
```
`config.py:80`：`APP_AUTH_TOKEN = os.getenv("APP_AUTH_TOKEN", "")` —— 默认空字符串。
`.env.example:13` 中 `# APP_AUTH_TOKEN=` 是注释掉的，即默认不配。

**待人工确认**：我**无法**确认服务器上 `APP_AUTH_TOKEN` 是否已设置——本次会话读取生产 `.env` 的 SSH 命令被权限策略拒绝。下方给出验证命令，请你自己跑一次。

**人话解释影响**：
这个中间件（middleware = 每个请求进来都先经过的一道检查）本意是"带对 token 才能访问 `/api/*`"。但它的写法是：**token 没配置就整道检查跳过**。也就是说，如果服务器 `.env` 里 `APP_AUTH_TOKEN` 是空的（默认就是空的），你的 API **在应用层面完全不设防**，全部安全性依赖 Nginx 那一层 Basic Auth（`deployment.md:205-206`）。

这叫"单点防御"。只要 Nginx 配置改错一次、或者有人从服务器内部/局域网直接访问 `127.0.0.1:8000`（绕过 Nginx），下面这些就全部敞开：

| 端点 | 位置 | 后果 |
|---|---|---|
| `POST /api/annotations/auto-batch` | `api/routes.py:440` | 调 DeepSeek v4-pro，`reasoning_effort=max`，**直接花你的钱** |
| `POST /api/annotations/auto` | `api/routes.py:419` | 同上 |
| `POST /api/tasks/scan` | `api/routes.py:89` | 触发全量扫描（拉所有外部数据源） |
| `POST /api/alerts/test-wechat` | `api/routes.py:245` | 往你的企业微信群发消息 |
| `DELETE /api/annotations/{id}` | `api/routes.py:410` | 删除你的人工标注数据 |

值得肯定的是：**已经用对了 `hmac.compare_digest`**（`api/app.py:329`）。这是防"计时攻击"（timing attack = 通过比较耗时的细微差异逐字符猜出密钥）的正确做法，比 `==` 强。写这段的人是懂的。

**修复建议**：
1. 在服务器 `.env` 里设一个长随机 `APP_AUTH_TOKEN`（`openssl rand -hex 32`），前端在 `localStorage` 里存同一个值（`frontend/src/api/client.ts:51-57` 已经实现了读取逻辑）。
2. 让"未配置"变得可见而不是静默：应用启动时若 `APP_AUTH_TOKEN` 为空，打一条 `logger.warning("APP_AUTH_TOKEN 未配置，/api/* 无应用层鉴权")`。这样你 `journalctl` 一眼能看到。
3. （不必做）改成"未配置就拒绝启动"会让本地开发很烦，不划算。

**验证方法**：
```bash
# 1) 服务器上确认 token 已配置（只看有没有，不打印值）
ssh mmon "grep -c '^APP_AUTH_TOKEN=.' /opt/market_monitor/.env"     # 期望 1

# 2) 关键一步：从服务器内部直连 8000 端口（绕过 Nginx），确认应用自己会拒绝
ssh mmon "curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/status"
#   期望 401。若返回 200，说明应用层无防护，全靠 Nginx。

# 3) 带 token 应能通过
ssh mmon "curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer <你的token>' http://127.0.0.1:8000/api/status"   # 期望 200
```

---

### P1-4 · 审计期间：本次扫描产物被并发提交并推送到公开仓库

> **说明责任**：`audit/raw/` 下的文件是**我这次审计生成的**。我没有执行任何 `git add` / `git commit` / `git push`。
> 但在我工作期间，你（或另一个并发会话）提交了两次并推送，`git add`/`commit -a` 把我的产物一并带上了。
> 这条发现因此既是"审计结果"，也是"审计副作用"——我必须原样报告。

**位置**：仓库提交 `7f6df34`（2026-07-10 22:26:32 +0800）与 `a82e7b7`（22:36:39 +0800），作者 `akis_white`

**证据**：

1. 这两个提交引入了我的扫描产物：
```
$ git log --oneline --diff-filter=A -- audit/raw/bandit.txt audit/raw/gitleaks.json audit/raw/server-pip-freeze.txt
a82e7b7 config(behavior): 覆盖门槛 0.5→0.95 + 证据表 ESS 收成单值
7f6df34 feat(behavior): 行为面板重画——全柱状统一 + 强度/净幅/情绪全方向拆分
```
```
$ git show --stat --format='' a82e7b7
 audit/raw/gitleaks.json                |  23 +++++++++++++++++++++++
 audit/raw/pip-audit-requirements.txt   |   3 +++
 audit/raw/server-pip-freeze.txt        | Bin 2794 -> 1323 bytes
 config.py                              |   5 ++++-
 ...
```

2. **已经推送到公开远端**（不是"还在本地"）：
```
$ git rev-parse HEAD        -> a82e7b783555f0990a9fc05c297230c8e4832f98
$ git rev-parse origin/main -> a82e7b783555f0990a9fc05c297230c8e4832f98
（两者相同 → 已同步到 github.com/mashiro1988/market_monitor，该仓库为 PUBLIC）
```

3. **公开出去的是什么**：

| 文件 | 内容 | 敏感度 |
|---|---|---|
| `audit/raw/server-pip-freeze.txt` | 服务器上 76 个 Python 包的**精确版本号** | **中** |
| `audit/raw/bandit.txt` | 8909 行扫描输出，含 `.gitignore` 排除的 `Pending_functions/` 下 8 个文件的路径与代码片段 | 低–中 |
| `audit/raw/gitleaks.json` | 泄露检测结果 | **无**（见下） |

`server-pip-freeze.txt` 现在公开可见的内容包括：
```
aiohttp==3.13.5        cryptography==48.0.0     paramiko==3.5.1
starlette==1.2.1       fastapi==0.136.3         uvicorn==0.49.0
```
`bandit.txt` 暴露的、原本被 `.gitignore:49`（`Pending_functions/`）排除的路径：
```
Pending_functions\ClsBinanceSymbol\cmc_categories.py
Pending_functions\crypto-binance-swap-candle\integrate_pickle_data.py
...（共 8 个文件）
```

4. **密钥没有二次泄露**——我当时用 `gitleaks detect --redact` 跑的，脱敏生效。已核对**提交进去的那个版本**（不是本地文件）：
```
$ git show HEAD:audit/raw/gitleaks.json | grep -E '"Secret"|"Match"'
  "Match": "api_key = \"REDACTED\"",
  "Secret": "REDACTED",
```

**人话解释影响**：
你现在等于在公开仓库贴了一张"我这台服务器的配料表"。攻击者拿到精确版本号后不用再试探——直接去漏洞库查 `starlette 1.2.1`、`aiohttp 3.13.5` 对应哪些已知漏洞，然后照着打。这个动作把"攻击者需要花时间摸清你的技术栈"这一步省掉了。

配合 P1-1 里已经公开的服务器地址 `47.243.252.92`，两条信息叠加起来价值明显高于各自单独存在。

**这不是"漏洞"**（没有代码缺陷），是**信息泄露**。P2-3 里我说过那 15 条依赖漏洞在你的代码里"不可达"——这个结论不变。P1-4 提高的是"被针对性扫描/尝试"的概率，不是"被打穿"的概率。

**修复建议**：
1. **立刻**：把 `audit/` 移出版本控制，并加进 `.gitignore`，防止后续三轮审计（Pass B/C/D）继续被扫进去：
```bash
git rm -r --cached audit/          # --cached = 只从版本控制移除，本地文件保留
printf '\n# ===== 审计报告与扫描原始输出（本地自用，不入库）=====\naudit/\n' >> .gitignore
git commit -m "chore: audit 产物移出版本控制"
git push
```
2. **决定是否清理历史**：这些内容已被推送、可能已被爬取。同 P0-1 的取舍——`git filter-repo` 能抹掉历史，但抹不掉已被缓存的副本。鉴于泄露的只是版本号（不是密钥），**我建议不折腾历史**，把精力花在 P2-3 的升级上：**把那些版本升上去，这张配料表就自动过期了**。
3. 顺带：`Pending_functions/` 既然被 gitignore，说明你不想公开它。可以确认一下里面是否有敏感内容（Pass C 死代码清理时一并看）。
4. **本报告 `audit/SECURITY.md` 本身不要提交到公开仓库**——它逐条列出了你所有的弱点和文件位置。修完再决定是否归档。上面第 1 步的 `.gitignore` 规则已覆盖它。

**验证方法**：
```bash
git ls-files audit/              # 期望：修复后无输出
git check-ignore -v audit/raw/bandit.txt   # 期望：命中 .gitignore 的 audit/ 规则
git log --oneline origin/main..HEAD        # 确认修复提交已推送后为空
```

---

### P2-1 · Linux 上残留的扫描锁永远清不掉 → 监控可能永久停摆

**位置**：`services/scan_runtime.py:88-106`（`_process_exists`）、`:145-158`（清锁逻辑）

**证据**：

```python
# services/scan_runtime.py:88
def _process_exists(pid: int) -> bool:
    """Return whether pid appears to be alive on Windows."""
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(...)    # :95  ← Linux 上 ctypes.windll 不存在
        ...
    except Exception:                                        # :105
        return True                                          # :106  ← 出错就当"进程还活着"
```
Linux 上 `ctypes.windll` 会抛 `AttributeError`，被 `:105` 捕获，`:106` 无条件返回 `True`。

再看清锁分支（`:145-158`）：
```python
lock_pid = _read_lock_pid()                 # :145
if lock_pid is not None:
    if not _process_exists(lock_pid):       # :147  Linux 上恒为 False → 永不进入清理分支
        ...清理残留锁...
    else:
        ...本次触发跳过...
elif age > SCAN_LOCK_STALE_SECONDS:         # :156  只有 pid 读不出来时才走"按时间判过期"
    ...清理...
```
关键：**只要锁文件里能读出 pid，Linux 上就永远走 `else`（跳过），连"按时间判过期"这条后路都走不到。**

**人话解释影响**：
`.scan.lock` 是一个"占位文件"，防止两个扫描同时跑。正常退出时会删掉。但如果进程被强杀（内存超限被系统 OOM kill、或 `kill -9`），文件会留下。

在 Windows 上，程序会检查"锁里记的那个进程号还活着吗"——不活着就清掉锁继续。但在 Linux（你的生产环境）上，这个检查**永远回答"活着"**。于是锁永远不被清理，**每一轮扫描都被跳过，监控静默停摆**——不报错、不告警，只是再也不更新数据了。

`docs/specs/deployment.md:291` 已经识别了这个问题，并用 systemd 的 `ExecStartPre=-/bin/rm -f .scan.lock` 缓解：每次服务启动前先删锁。所以**只要服务重启过，就能自愈**。残留风险只在"进程被杀但 systemd 没重启服务"这种情况——`Restart=always` 又覆盖了大部分。所以定为 P2，不是 P1。

**修复建议**：
把 `_process_exists` 换成跨平台写法（几行的事）：
```python
def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        ...保留现有 windll 逻辑...
    try:
        os.kill(pid, 0)      # 信号 0 = 只检查存在性，不真的杀
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # 进程存在但不属于当前用户
```

**验证方法**：
```bash
# 在服务器上跑（当前会返回 True = 有 bug；修复后应返回 False）
ssh mmon "cd /opt/market_monitor && .venv/bin/python -c \
  'from services.scan_runtime import _process_exists; print(_process_exists(999999))'"
# 期望修复后：False
```

---

### P2-2 · 新闻源异常被静默吞掉 → 数据悄悄丢失、监控失明

**位置**：`scanners/sources/rss_source.py:108`、`scanners/sources/jin10_source.py:168`、`scanners/sources/rss_source.py:83`、`alerts/channels/wechat_work.py:71`

**证据**：

bandit 报出（`audit/raw/bandit.txt`）：
> `[B112:try_except_continue]` Location: `.\scanners\sources\rss_source.py:108`
> `[B112:try_except_continue]` Location: `.\scanners\sources\jin10_source.py:168`
> `[B110:try_except_pass]` Location: `.\scanners\sources\rss_source.py:83`

代码（`rss_source.py`，每条 RSS 条目一个循环）：
```python
            except Exception:      # :108
                continue           # ← 整条新闻被丢弃，没有任何日志
```
`scanners/sources/jin10_source.py:168` 同构。

`alerts/channels/wechat_work.py:70-72`：
```python
        except Exception:          # :71
            return False           # ← 发送失败，不记日志
```

**人话解释影响**：
`except Exception: continue` 的意思是"这条出了任何错，就当没看见，跳过"。用在解析新闻的循环里，后果是：**如果金十或某个 RSS 源改了字段格式，你的系统会一条不落地把它们全部丢掉，而且完全不出声**。你看到的现象只是"最近新闻怎么变少了"，日志里干干净净——这正是审计术语里的"监控失明"（你的监控系统自己瞎了，却告诉你一切正常）。

`alerts/channels/wechat_work.py:71` 稍好：`send()` 主方法（`alerts/channels/wechat_work.py:53-55`）是记日志的，且 `_dispatch` 会把 `delivered=False` 写进 `alert_logs` 表。只有 `send_text()` 这个次要方法是纯吞。

**修复建议**：
1. 把 `continue` 前加一行 `logger.debug("跳过一条 {} 条目: {}", self.name, exc)`，并在循环外统计 `skipped` 条数、`logger.info` 出来。这样"丢了多少"是可见的。
2. `alerts/channels/wechat_work.py:71` 补 `logger.error`。
3. 更进一步（可选）：如果某个源本轮 `skipped / total > 50%`，直接推一条企业微信告警——"源格式可能变了"。

**验证方法**：
修复后，人为构造一个坏条目（在测试里 mock 一个缺 `title` 的 entry），跑 `pytest tests/test_rss_source.py -s`，确认日志中出现跳过计数。当前代码下该计数不存在。

---

### P2-3 · 后端依赖存在已知漏洞（经可达性分析，均不可达）

**位置**：已部署环境的依赖清单（`audit/raw/server-pip-freeze.txt`）

**证据**：`pip-audit` 对**服务器实际安装的版本**扫描（原始输出 `audit/raw/pip-audit-server.txt`）：

> Found 15 known vulnerabilities in 4 packages

| 包 | 已装版本 | 漏洞数 | 修复版本 | 在本项目**是否可达** |
|---|---|---|---|---|
| `aiohttp` | 3.13.5 | 10 | 3.14.1 | **不可达** |
| `cryptography` | 48.0.0 | 1 (GHSA-537c-gmf6-5ccf) | 48.0.1 | 仅 SFTP 后端用（待确认） |
| `paramiko` | 3.5.1 | 1 (CVE-2026-44405) | 无 | 仅 SFTP 后端用（待确认） |
| `starlette` | 1.2.1 | 2 (PYSEC-2026-248/249) | 1.3.1 | **不可达** |

可达性分析（我实际 grep 过，不是猜的）：
- **aiohttp**：只作为 `ccxt` 的传递依赖被装上。本项目 `grep -rE 'async_support|ccxt\.async|import aiohttp'` **无任何命中**，只有 `scanners/sources/okx_source.py:12` 的同步 `import ccxt`。且这批漏洞多为"aiohttp 作为**服务端**时的 DoS"，本项目根本没跑 aiohttp 服务端。→ 不可达。
- **starlette**：PYSEC-2026-249 需要调用 `request.form()`；PYSEC-2026-248 需要读 `request.url.hostname`。`grep -rE 'request\.form\(|\.url\.hostname|\.url\.netloc'` **无命中**（中间件只读 `request.url.path`）。项目无文件上传、无 WebSocket。→ 不可达。
- **cryptography / paramiko**：只在 `services/remote_fs.py` 走 SFTP 时使用。按 `deployment.md:315` 生产应为 `REMOTE_BACKEND=local`，即不连 SFTP。**待人工确认**（见 P1-2 的验证命令）。

对 `requirements.txt`（声明的版本范围）单独扫描（`audit/raw/pip-audit-requirements.txt`）：
> Found 1 known vulnerability in 1 package — `paramiko 3.5.1 / CVE-2026-44405`（rsakey.py 允许 SHA-1 算法，无修复版本）

**人话解释影响**：
"依赖漏洞"是指你用的第三方库本身被发现有毛病。但**有毛病 ≠ 打得到你**——就像你家买的锁有个"用特定钥匙能撬开"的缺陷，可你把这把锁装在了一个根本没有门的墙上。上面 15 条里，12 条落在你压根没启用的功能上。

真正建议升级的理由不是"现在危险"，而是"下次你启用某个功能时，别踩到已知的坑"。

**修复建议**：
常规升级即可，不紧急：
```bash
.venv/bin/pip install -U 'aiohttp>=3.14.1' 'cryptography>=48.0.1' 'starlette>=1.3.1'
```
注意 `starlette` 由 `fastapi` 约束，直接升可能冲突——先 `pip install -U fastapi` 让它带上新 starlette。`paramiko` 的 CVE 目前**无修复版本**，若确认走 `local` 后端，可考虑把 `paramiko` 从 `requirements.txt:11` 移除（留到 Pass C 决策）。

**验证方法**：
```bash
ssh mmon "/opt/market_monitor/.venv/bin/pip freeze" > /tmp/freeze.txt
pip-audit -r /tmp/freeze.txt --no-deps --desc
# 期望：aiohttp / cryptography / starlette 三项消失
```

---

### P2-4 · 前端依赖存在已知漏洞（1 critical + 3 high，但全部为 dev-only 或不可达）

**位置**：`frontend/package.json`、`frontend/package-lock.json`

**证据**：`npm audit`（原始输出 `audit/raw/npm-audit.txt` / `.json`）：
> 10 vulnerabilities (1 low, 5 moderate, 3 high, 1 critical)

我从 `package-lock.json` 逐个读了 `dev` 标记（**这是决定性证据**）：

| 包 | 版本 | npm 严重级 | lockfile `dev` 标记 | 是否进入部署产物 |
|---|---|---|---|---|
| `vitest` | 2.1.9 | **critical** | `dev: true` | **否**（测试运行器） |
| `vite` | 6.4.2 / 5.4.21 | **high** | `dev: true` | **否**（构建工具） |
| `ws` | 8.20.0 | **high** | `dev: true` | **否** |
| `form-data` | 4.0.5 | **high** | `dev: true` | **否** |
| `esbuild` | 0.25.12 / 0.21.5 | moderate | `dev: true` | **否** |
| `@babel/core` | 7.29.0 | low | `dev: true` | **否** |
| `react-router` | 6.30.3 | moderate | **生产依赖** | **是** |

`frontend/package.json` 的运行时依赖只有 6 个：`@tanstack/react-query`、`lucide-react`、`react`、`react-dom`、`react-router-dom`、`recharts`。

唯一上线的 `react-router` 漏洞：*"same-origin redirect with path starting `//` causes open redirect"*（开放重定向 = 攻击者构造链接让你的站把访客弹去恶意站点）。它要求**应用拿用户可控的值去做跳转**。本项目全站只有一处跳转，目标写死：
```tsx
// frontend/src/main.tsx:29
{ index: true, element: <Navigate to="/market" replace /> },
```
→ **不可达**。且整站在 Nginx Basic Auth 之后，外人根本点不进来。

**人话解释影响**：
`npm audit` 报"1 个 critical"很吓人，但它不区分"这个包会跑在服务器上"和"这个包只在你本机敲 `npm test` 时跑"。前端的构建流程是：本机/服务器上 `npm run build` 把 React 代码编译成几个静态 `.js` 文件放进 `frontend/dist/`，然后 FastAPI 只负责把这几个文件发给浏览器（`api/app.py:342-344`）。`vitest`、`vite`、`ws` 这些**从来不会被部署、不会被访客碰到**。

所以：这一栏的真实风险≈0，属于"该升就升"的卫生问题。

**修复建议**：
```bash
cd frontend && npm audit fix        # 不带 --force，避免破坏性大版本跳跃
```
跑完后 `npm run build && npx vitest run` 确认前端仍能构建、测试仍绿。`--force` 会把 `vite` 跨大版本升级，可能弄坏构建——不值得，因为收益是 0。

**验证方法**：
```bash
cd frontend && npm audit --omit=dev     # 只看生产依赖
# 期望：仅剩 react-router 那条 moderate（或修复后 0 条）
```

---

### P2-5 · `hmac.compare_digest` 遇非 ASCII 请求头会抛异常 → 返回 500 而非 401

**位置**：`api/app.py:329`

**证据**：

```python
supplied = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
supplied = supplied or request.headers.get("x-app-token", "").strip()
if not hmac.compare_digest(supplied, token):     # :329
```
`supplied` 完全由请求方控制。我实测了 Python 的行为：
```
supplied='abc'   -> returns False              （正常，返回 401）
supplied='é'     -> TypeError: comparing strings with non-ASCII characters is not supported
supplied='中文'  -> TypeError: 同上
```
该 `TypeError` 会冒泡到 `api/errors.py:42` 的 `unhandled_error_handler`，返回 **500 INTERNAL_ERROR** 并 `logger.exception` 打一条完整堆栈。

**人话解释影响**：
这**不是**鉴权绕过——异常导致请求失败，攻击者拿不到数据（安全术语叫"失败关闭"，fail closed，是好事）。

实际影响有两个，都很轻：
1. 任何人往 `Authorization` 头里塞一个中文字符，就能让服务器打一条错误堆栈到日志。反复发就能刷满你的 `logs/market_monitor.log`（有 20MB 轮转，`config.py:111`，所以也淹不死）。
2. 返回 500 而不是 401，语义不对，排查时会误导你以为服务器坏了。

**修复建议**：
比较前先编码成字节：
```python
if not hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
```
`compare_digest` 对 `bytes` 没有 ASCII 限制，且仍是恒定时间比较。

**验证方法**：
```bash
# 修复前：500    修复后：401
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer é' \
     http://127.0.0.1:8000/api/status
```
（需先设置 `APP_AUTH_TOKEN`，否则中间件整段跳过，见 P1-3。）

---

### P2-6 · 企业微信 webhook 预览"不泄密"靠字符串长度巧合，不是设计保证

**位置**：`services/alerts_service.py:30-33`，经由 `api/routes.py:235` 的 `GET /api/alerts/webhook-status` 对外暴露

**证据**：

```python
# services/alerts_service.py:30
def get_webhook_status() -> AlertWebhookStatus:
    webhook = config.WECHAT_WORK_WEBHOOK
    preview = f"{webhook[:50]}..." if webhook else None      # :32
    return AlertWebhookStatus(configured=bool(webhook), preview=preview)
```
企业微信 webhook 的 URL 形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<密钥>`。我实测：
```
'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=' 长度 = 53
webhook[:50]  ->  'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?k'
是否切到 key?  ->  False
```
**当前是安全的**——密钥从第 53 个字符开始，`[:50]` 刚好够不着，差 3 个字符。

**人话解释影响**：
这个接口的本意是让前端显示"webhook 配了没、长什么样"。它取 URL 的前 50 个字符。而企业微信的 URL 恰好在第 53 个字符才开始放密钥。**你侥幸躲过了 3 个字符。**

风险在于这是巧合而非设计：如果哪天企业微信换个更短的域名/路径，或者你把这个函数复用到别的 webhook（比如 Slack 的 `https://hooks.slack.com/services/T00/B00/<密钥>`，密钥位置早得多），`[:50]` 就会**把密钥的一部分或全部返回给 API 调用方**。

而且这个端点在 `/api/*` 下——若 P1-3 的 token 没配，它是**无鉴权**的。

**修复建议**：
别切字符串，明确地只回不敏感的部分：
```python
from urllib.parse import urlsplit
def get_webhook_status() -> AlertWebhookStatus:
    webhook = config.WECHAT_WORK_WEBHOOK
    if not webhook:
        return AlertWebhookStatus(configured=False, preview=None)
    parts = urlsplit(webhook)
    preview = f"{parts.scheme}://{parts.netloc}{parts.path}"   # 丢掉 query（key 在这里）
    return AlertWebhookStatus(configured=True, preview=preview)
```

**验证方法**：
```bash
curl -s http://127.0.0.1:8000/api/alerts/webhook-status | python -c \
  "import json,sys; p=json.load(sys.stdin)['preview']; print('KEY LEAKED' if 'key=' in (p or '') and len(p.split('key=')[-1].strip('.'))>0 else 'OK')"
# 期望 OK
```

---

### P2-7 · `REMOTE_ALLOW_UNKNOWN_HOST=1` 会关闭 SSH 主机指纹校验（默认安全）

**位置**：`services/remote_fs.py:132-147`（关键行 `:145`）

**证据**：

bandit 报为 **High**（`audit/raw/bandit.txt`）：
> `[B507:ssh_no_host_key_verification]` Paramiko call with policy set to automatically trust the unknown host key.
> Severity: **High** Confidence: Medium · Location: `.\services\remote_fs.py:145:8`

代码：
```python
    if _allow_unknown_host_keys():
        logger.warning("REMOTE_ALLOW_UNKNOWN_HOST=1: SFTP 将自动信任未知主机指纹，仅建议临时调试使用")
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())     # :145
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())      # :147  ← 默认走这里
```
`:133`：`os.getenv("REMOTE_ALLOW_UNKNOWN_HOST", "0")` —— **默认 `0`**，走 `RejectPolicy`（拒绝未知主机）。`.env.example:54` 也写着 `REMOTE_ALLOW_UNKNOWN_HOST=0` 并注明"生产保持 0"。

**我把 bandit 的 High 降级为 P2**，理由：默认配置是安全的，且已经打了警告日志。bandit 是静态工具，看到 `AutoAddPolicy` 就报 High，它不知道这条分支默认走不到。

**人话解释影响**：
SSH 连接时会核对对方服务器的"指纹"，确认你连的是那台机器而不是中间人假冒的。`AutoAddPolicy` = "谁应答我就信谁"，此时中间人攻击（有人在网络路径上冒充服务器）可以截获你的 SFTP 凭据和数据。

设计是对的——默认拒绝、要显式开、开了还打警告。风险只在"某次调试把它设成 1，然后忘了改回来"。

**修复建议**：
不用改代码。运维层面确认：
```bash
ssh mmon "grep '^REMOTE_ALLOW_UNKNOWN_HOST=' /opt/market_monitor/.env"   # 期望 0 或该行不存在
```
可选加固：`_allow_unknown_host_keys()` 里再加一层"仅当 `REMOTE_HOST` 是私网地址时才允许开启"。收益低，不急。

**验证方法**：见上方命令。期望输出 `REMOTE_ALLOW_UNKNOWN_HOST=0` 或无输出。

---

### P2-8 · 无任何速率限制，昂贵端点可被反复调用

**位置**：`api/app.py:288-336`（`create_app` 内只注册了一个鉴权中间件，无限流中间件）；受影响端点见 P1-3 表格

**证据**：全仓库搜索限流相关关键字无命中：
```bash
$ grep -rnE 'slowapi|RateLimit|limiter|Throttle' --include=*.py .
（无输出）
```
`api/app.py:311-334` 注册的中间件只有 4 个异常处理器 + 1 个 `optional_api_auth`。

**人话解释影响**：
只要过了鉴权那道门（或者根本没门，见 P1-3），任何人都能无限次调 `POST /api/annotations/auto-batch`。每次调用都会请求 DeepSeek 的 `v4-pro` 推理模型、`reasoning_effort=max`、`read_timeout=600s`（`config.py:89-92`）——**每一次都是真金白银**。

这不是传统意义的"安全漏洞"（数据不会泄露），而是"财务 DoS"：把你的 API 账单打爆。

**为什么只是 P2**：需要先穿过 Nginx Basic Auth。单人仪表盘、正常没人知道地址。风险与"你自己的浏览器点太快"差不多。

**修复建议**（按性价比）：
1. **最便宜**：在 Nginx 层面对昂贵路径限流，无需改代码：
```nginx
limit_req_zone $binary_remote_addr zone=llm:10m rate=6r/m;
location /api/annotations/auto { limit_req zone=llm burst=3; proxy_pass http://127.0.0.1:8000; ... }
```
2. 在 DeepSeek 账户上设消费上限（平台侧硬约束，最可靠）。
3. 应用层加 `slowapi`——为一个单用户系统引入新依赖，性价比不高，不推荐。

**验证方法**：
```bash
for i in $(seq 1 10); do
  curl -s -o /dev/null -w '%{http_code} ' -X POST https://<域名>/api/tasks/scan -u user:pass
done; echo
# 加限流后：期望出现 429（Too Many Requests）
```

---

## d. 工具报了、但**不是**问题（误报澄清）

审计的价值一半在"找到问题"，一半在"证明某些警报不用管"。以下三条是工具的假阳性，**不需要修**：

### 误报-1 · bandit B105「Possible hardcoded password: '10'」

- **bandit 报的位置**：`.\config.py:423`
- **当前实际位置**：`config.py:426`
- **为什么行号对不上（已查明，不是工具 bug）**：bandit 于 22:20 扫描时，`config.py` 共 589 行；22:36:39 的提交 `a82e7b7` 在第 202 行附近插入了 3 行（`BEHAVIOR_COVERAGE_MIN` 相关），文件变成 592 行，**202 行之后的所有行号整体 +3**。bandit 报的 423 对当时的文件是正确的，对现在的文件是 426。核对方式：
  ```
  $ git show 1c93d26:config.py | wc -l   -> 589
  $ git show HEAD:config.py | wc -l      -> 592
  $ git diff 1c93d26 HEAD -- config.py | grep '^@@'   -> @@ -202,7 +202,10 @@
  ```
- **实际内容**：`"min_token_count": 10,` —— 板块告警规则的"最少成分币数量"
- **为什么误报**：bandit 看到字典的键名里含有 `token` 三个字母，就怀疑它是密码。`min_**token**_count` 说的是"代币（token）数量"，跟认证令牌毫无关系。
- **处理**：忽略。若想让扫描输出干净，可加 `# nosec B105` 注释。

### 误报-2 · bandit B324「Use of weak MD5 hash for security」（报为 High）

- **位置**：`scanners/sources/rss_source.py:99`
- **实际内容**：
```python
source_id=str(hashlib.md5(source_fingerprint.encode()).hexdigest()),
```
- **为什么误报**：MD5 作为**密码/签名**算法确实已被攻破。但这里它的用途是给每条 RSS 新闻算一个**内容指纹用于去重**（同一条新闻不要入库两遍）。没有攻击者会为了"让两条新闻碰撞成同一个 ID"去构造 MD5 碰撞——那样做的收益是"让你少存一条新闻"。
- **处理**：可选地写成 `hashlib.md5(..., usedforsecurity=False)`，纯粹为了让扫描器闭嘴。功能不变。

### 误报-3 · `database.py` 里 f-string 拼接的 SQL —— **不是 SQL 注入**

- **位置**：`database.py:58`、`:76`、`:99`、`:126`
- **实际内容**：
```python
conn.execute(text(f"ALTER TABLE behavior_segments ADD COLUMN {column_name} {column_type}"))
```
- **为什么看着像注入、实际不是**：SQL 注入的前提是"拼进去的值来自外部输入"（用户填的表单、URL 参数等）。这里的 `column_name` / `column_type` 全部来自**同一个函数体内几行上方写死的字典字面量**：
```python
for column_name, column_type in {
    "human_class": "VARCHAR(30)",
    "human_confirmed_at": "DATETIME",
}.items():
```
外部输入**根本进不来**。这是 SQLite 的限制（`ALTER TABLE` 的列名不能用绑定参数），标准做法。
- **顺带确认**：全项目**没有任何一处**把外部输入拼进 SQL。所有数据库访问走 SQLAlchemy ORM 的绑定参数。`alerts/dispatch.py:37` 的 `.like(f"%{exact_marker}%")` 里 `exact_marker` 是内部常量，且 `.like()` 本身会把值作为参数绑定，不拼进 SQL 文本。

---

## e. 资产盘点（审计流程第 1 步的产物）

### 技术栈
- **后端**：Python 3.11 · FastAPI + uvicorn（单 worker）· SQLAlchemy ORM + SQLite（WAL 模式）· APScheduler（进程内定时器）· loguru
- **前端**：React 18 + TypeScript + Vite，构建成静态文件由 FastAPI 直接托管
- **部署**：腾讯云日本 CVM · systemd 常驻 · Nginx 反向代理（TLS + Basic Auth）→ `127.0.0.1:8000`

### 程序入口
| 入口 | 位置 | 说明 |
|---|---|---|
| `python run.py app` | `run.py:98` | 桌面用法，自动开浏览器 |
| `uvicorn api.app:app` | `api/app.py:390` | **生产入口**，自带调度器 |
| `api.app:dev_app` | `api/app.py:391` | 开发用，**关掉**调度器 |
| `python run.py scan` | `run.py:207` | 手动跑一次扫描 |
| `deploy.sh` | `deploy.sh` | 服务器更新脚本（备份 → pull → 装依赖 → 构建前端 → 重启） |

### 全部对外交互面

**① 出站 API 调用**（程序主动去别人家取数据）

| 目标 | 是否需要密钥 | 代码位置 |
|---|---|---|
| Yahoo Finance（股指/期货/商品/美元指数） | 否 | `scanners/sources/yfinance_source.py` |
| OKX（加密现货 + 休市补点用永续） | 否 | `scanners/sources/okx_source.py` |
| CNBC RSS + CNBC 债券收益率 | 否 | `scanners/sources/rss_source.py` / `cnbc_bond_source.py` |
| 东方财富（债券） | 否 | `scanners/sources/eastmoney_bond_source.py` |
| 金十数据 | 否 | `scanners/sources/jin10_source.py` |
| InvestingLive / FinancialJuice RSS | 否 | `config.py:330-344` |
| Polymarket（预测市场） | 否 | `scanners/sources/polymarket/` |
| **CoinMarketCap** | **`CMC_API_KEY`** | `services/cmc_client.py` |
| **DeepSeek**（新闻打标 + 自动标注） | **`DEEPSEEK_API_KEY`** | `services/news_tagging.py:23`、`services/annotation_service.py:302` |
| **Dune Analytics** | **`DUNE_API_KEY`**（休眠，app 不加载） | `onchain_data/dune_queries.py` |

**② 密钥清单**（全部从 `.env` 读，`.env` 已正确 gitignore 且**从未被提交**）

`DEEPSEEK_API_KEY` · `CMC_API_KEY` · `WECHAT_WORK_WEBHOOK` · `APP_AUTH_TOKEN` · `DUNE_API_KEY` · `REMOTE_PASSWORD` / `REMOTE_KEY_PATH`

**③ 数据库**：SQLite `market_monitor.db`（`config.py:100`），WAL 模式（`database.py:18-25`），15s 忙等超时

**④ 文件读写**：`logs/`（20MB 轮转、14 天保留）· `data/remote_cache/`（远程 pkl 缓存）· `frontend/dist/`（静态产物）· `.scan.lock`（扫描互斥锁）· `backups/`（deploy.sh 备份）

**⑤ 监听端口**：uvicorn 绑 **`127.0.0.1:8000`**（`run.py:108`，不面向公网）；公网由 Nginx 443/80 接管

**⑥ 定时任务**（全部跑在 FastAPI 进程内，`api/app.py:194-283`）

| 任务 | 频率 | 用途 |
|---|---|---|
| `scan_cycle` | 5 分钟 | 拉价格/新闻/预测市场 |
| `behavior_cycle` | 5 分钟（错峰 +2min） | 价格行为分段与共振分 |
| `hourly_summary` | 1 小时 | 企业微信小时报 |
| `remote_data_cycle` | 1 小时 | 拉 BMAC pivot → 板块扫描 |
| `gap_repair` | 每小时第 37 分 | 补价格快照缺口 + 新闻打标 |
| `data_retention` | 每日 03:17 | 清理过期快照 |
| `behavior_daily_summary` | 每日 UTC 00:05 | 昨日行为日报 |
| `cmc_bootstrap` / `cmc_refresh` | 启动后 10s / 每周一 02:17 | 刷新板块映射 |

**⑦ 通知渠道**：企业微信 Webhook（`alerts/channels/wechat_work.py`）· 控制台（`alerts/channels/console.py`）

**⑧ 入站接口**：`/api/*` 约 30 个端点（`api/routes.py`）+ SPA 静态托管。鉴权见 P1-3。

---

## f. 关于「金额与精度」和「幂等性」的专项说明

审计提纲要求专门检查这两项。结论如下。

### 金额与精度：**不适用**

本项目**不进行任何金额计算**——不下单、不转账、不记账、不结算。价格（`float`）的全部用途是：
- 存进 `price_snapshots` 表供图表展示；
- 与阈值比大小（`alerts/evaluators/price.py:48`：`abs(move.change_pct) >= threshold`）；
- 计算涨跌百分比（`alerts/evaluators/price.py:206`：`(current_price - base.price) / abs(base.price) * 100`）。

浮点数的舍入误差量级在 1e-15 相对误差，而行情本身的最小跳动（tick）远大于此。**用 `Decimal` 在这里没有收益**。CPA 直觉里"算钱不能用 float"是对的，但前提是"在算钱"——这里没有。

### 幂等性（同一行情事件会不会重复推送）：**待人工确认**

**事实（有证据）**：

1. 四条价格/预测类告警规则的冷却期被显式设为 **0 分钟**：`config.py:383`（`btc_price_spike`）、`:391`（`eth_price_spike`）、`:399`（`us_futures_spike`）、`:415`（`prediction_shift`）。

2. 冷却期为 0 时，冷却机制**等价于关闭**。`alerts/dispatch.py:50-53`：
```python
cutoff = datetime.now(...) - timedelta(minutes=cooldown_minutes)   # cooldown=0 → cutoff == now
delivered = self._delivered_channels_since(rule_name, cutoff, ...) # 查 timestamp >= now 的日志
return required.issubset(delivered) if required else bool(delivered)
```
我实测验证：`cutoff == now` 时，1 秒前刚发出的告警 `past_log >= cutoff` 为 `False` → 查不到任何记录 → `delivered = set()` → `{'wechat_work'}.issubset(set())` = `False` → **判定"不在冷却期" → 每一轮都发**。

3. 扫描每 5 分钟一轮（`config.py:118-122`），而价格窗口是 15 分钟且**滑动**。因此同一次异动可能连续落在 2–3 个扫描周期的窗口内，各触发一次推送。

4. 唯一的抑制机制是"陈旧数据保护"（`config.py:448`：`ALERT_PRICE_MAX_STALENESS_MINUTES = 30`，逻辑在 `alerts/evaluators/price.py:123-133`）——它只阻止对**超过 30 分钟的旧 bar**反复告警，不阻止对新 bar 的重复窗口告警。

**我不判定这是 bug**。理由：`cooldown_minutes: 0` 是显式写死的值，`sector_spike` 和 `hourly_summary` 却设了 55 分钟——说明作者清楚这个字段的作用，对价格类**有意选择**了 0。一次快速异动被推 2 次，对盯盘场景可能正是想要的。

**请你确认**：BTC 一次 0.5% 的快速拉升，你希望收到 1 条还是 2–3 条企业微信？
- 若希望 1 条：把这四条规则的 `cooldown_minutes` 改成 15（= 窗口长度），重复即消失。
- 若当前行为符合预期：无需改动，本条关闭。

---

## g. 工具执行情况（透明度声明）

| 工具 | 用途 | 状态 | 原始输出 |
|---|---|---|---|
| `pip-audit` 1.x | Python 依赖已知漏洞 | ✅ 已跑（服务器实际版本 + `requirements.txt` 各一次） | `audit/raw/pip-audit-server.txt`、`audit/raw/pip-audit-requirements.txt`、`audit/raw/server-pip-freeze.txt` |
| `bandit` 1.9.4 | Python 代码安全模式扫描 | ✅ 已跑（Python 3.11.7） | `audit/raw/bandit.txt`、`audit/raw/bandit.json` |
| `gitleaks` 8.30.1 | 泄露密钥（**含完整 git 历史**） | ✅ 已跑（240 个提交，3.77 MB） | `audit/raw/gitleaks.json` |
| `npm audit` | 前端依赖已知漏洞 | ✅ 已跑 | `audit/raw/npm-audit.txt`、`audit/raw/npm-audit.json` |
| `semgrep` | 深度模式扫描 | ⏭️ **跳过**（可选项，未安装） | — |
| `trufflehog` | 泄露密钥（备选） | ⏭️ **跳过**（已用 gitleaks 覆盖同一职责） | — |

**本次审计中被环境阻断的一步**：读取生产服务器 `/opt/market_monitor/.env` 与 Nginx / systemd 配置的 SSH 命令被权限策略拒绝。因此以下三项**未能由我直接验证**，已在正文中标注「待人工确认」，并给出了可直接复制执行的验证命令：
1. 服务器上 `APP_AUTH_TOKEN` 是否已设置（影响 P1-3 的实际严重性）
2. 服务器上 `REMOTE_BACKEND` 是否为 `local`（影响 P1-2、P2-3 的可达性）
3. 服务器上 `REMOTE_ALLOW_UNKNOWN_HOST` 是否为 `0`（影响 P2-7）

**关于行号（重要）**：本次审计**期间仓库被并发修改**（见 P1-4）。提交 `a82e7b7` 在 `config.py` 第 202 行附近插入 3 行，导致该文件 202 行之后的行号整体 +3。因此：
- `bandit` 于 22:20 输出的 `config.py` 行号，对应的是**旧版本**（589 行）；
- 本报告中**所有** `file:line` 均已在提交 `a82e7b7` **之后**用 `sed -n 'Np' <file>` 逐条直读复核，**对应当前 HEAD（`a82e7b7`）**。
- 若你在读本报告时又有新提交，行号可能再次漂移；用报告里给出的**代码内容**（而非行号）定位最可靠。

**关于"只审不改"**：本次会话**未修改、未暂存、未提交、未推送任何文件**，仅新建了 `audit/SECURITY.md` 与 `audit/raw/*`。已用不依赖缓存的 git 底层命令核验：
```
$ git diff --stat HEAD        -> 无输出（已追踪文件零改动）
$ git diff --name-only HEAD | wc -l  -> 0
```
但**仓库本身在审计期间被外部并发提交了两次并推送**（`7f6df34`、`a82e7b7`，作者 `akis_white`），这两次提交把 `audit/raw/` 扫进了公开仓库——即 P1-4。

**自检记录**（提交前按提纲要求执行）：
- 已重读全文，删除了 2 条无 `file:line` 证据的初稿条目。
- 已用脚本抽取全文 83 处 `file:line` 引用逐条核验文件存在性与行号范围；9 处"未命中"经复核全部为裸文件名（7 处位于 bandit 原始输出的逐字引用中，1 处 `test.py:14` 系已删除文件、正是 P0-1 的发现本身，1 处已补全为完整路径）。
- 3 条被工具报为 High/Critical 的项经可达性分析后降级：bandit B507（`services/remote_fs.py:145`，默认配置安全 → P2-7）、bandit B324（`scanners/sources/rss_source.py:99`，非安全用途 → 误报-2）、npm `vitest` critical（lockfile 标记 `dev: true`，不进产物 → P2-4）。
- 初稿曾把 `config.py` 的行号偏差归因为"工具 bug"。复核时查明真实原因是**审计期间文件被并发修改**，已更正（见「关于行号」与误报-1）。此处记录以示：该结论是被推翻后重写的，不是一开始就对。
- 未使用任何来自记忆的 CVE 编号；所有 CVE / GHSA / PYSEC 编号均系转述 `pip-audit` 与 `npm audit` 的原始输出。
- 「幂等性」一条虽有完整证据链，仍因涉及用户已校准的配置值而降级为「待人工确认」，未判定为缺陷。
- P1-4 是审计过程本身的副作用，未因"这条对我不利"而隐去。
