# market_monitor 公网部署设计（腾讯云日本 · 2C4G · Ubuntu）

> 目标：把本地单机运行的 market_monitor 部署到一台腾讯云日本 2 核 4G CVM（Ubuntu/Debian），
> 通过**自有域名 + HTTPS + Basic Auth** 从公网安全访问。本文是设计稿兼可执行 Runbook，
> 用户在服务器上自行复制粘贴执行。
>
> 最近更新：2026-06-03（首版）。

## 1. 目标与约束

- **访问方式**：浏览器经域名 `https://<your-domain>` 访问，Basic Auth 弹窗鉴权。
- **运行方式**：systemd 常驻、开机自启、崩溃重启。
- **数据库**：全新 SQLite，启动后自动回补最近 ~72h 价格/新闻。
- **代码来源**：公开仓库 `https://github.com/mashiro1988/market_monitor`（`main` 分支），直接 `git clone`。
- **执行者**：用户本人在服务器上执行本 Runbook 的命令。

**核心约束（必须遵守，否则会出错）：**
1. **只能单 worker**：APScheduler 跑在 FastAPI 进程内，`task_service._TASKS` 是内存全局变量。多 worker
   会重复扫描、重复告警、重复 SFTP 拉取、重复发企业微信。→ 跑**一个** uvicorn 进程，**不加** `--workers`。
2. **先构建前端再启动服务**：`api.app` 在导入时检测 `frontend/dist/assets` 是否存在来决定挂载静态资源。
   dist 不在仓库里，必须先 `npm run build` 再启动/重启 uvicorn。
3. **uvicorn 只绑 `127.0.0.1`**：公网由 Nginx 接管（TLS + Basic Auth），应用进程不直接面对公网。

> **共存说明（本机实测，2026-06-03）**：这台 CVM 已用 **PM2** 跑 `coin-realtime-data` / `qronos` 等 crypto 服务。结论：可安全共存，对本 spec 只需以下调整——
> - **端口无冲突**：80 / 443 / 8000 / 4780 / 1080 全空闲、Nginx 未安装 → 按 spec 全新装即可。
> - **跳过建 swap**：已有 ~5.9GB swap、可用内存 2.7GB → Phase 1 建 swap 一步省掉。
> - **两套进程管理器并存**：他们用 PM2，本应用用 **systemd**，互不干扰；本应用统一 `systemctl`/`journalctl` 管理，**别**塞进 PM2。
> - **给 systemd 加 `MemoryMax=2G`**：共享机器上限制本应用内存尖峰（加载 BMAC pivot 时），避免波及对方的实时采集（仍有 swap 兜底）。
> - **留意出口 IP 限频**：两应用从同一公网 IP 访问交易所/行情源（OKX/CoinGecko/CMC 按 IP 限频）。本应用 5 分钟级、量小，正常无碍；若现 429 再错峰/加重试。

## 2. 架构拓扑

```
 浏览器（国内，直连日本，无需代理）
        │  https://<your-domain>     ← Basic Auth
        ▼
 腾讯云日本 CVM (Ubuntu, 2C4G)
   [安全组]  入站放行 22/80/443      ← 控制台手动配，最易漏
   [ufw]    放行 22/80/443
        ▼
   [Nginx :443]
     ├─ Let's Encrypt TLS（自动续期）
     ├─ HTTP Basic Auth（全站，含 /api）
     ├─ 80 → 443 跳转
     └─ proxy_pass → 127.0.0.1:8000  (proxy_read_timeout 600s)
        ▼
   [uvicorn api.app:app :8000]  ← systemd, 单 worker, 仅绑 127.0.0.1
     进程内 APScheduler:
       scan_cycle(5m) · hourly_summary(1h) · remote_data_cycle(1h) · cmc_bootstrap
        │
        ├─► SQLite market_monitor.db (WAL)，路径相对 WorkingDirectory
        ├─► 出站（全部直连，无代理）：
        │    yfinance / OKX / CoinGecko / CNBC / Polymarket / Dune / CMC
        │    东方财富(债券) / 企业微信 webhook
        └─► 本地读盘（无网络 / 无 SSH，REMOTE_BACKEND=local）：
             /home/ubuntu/data/firm/coin-realtime-data_v1.1.11/data/ 内 BMAC pivot
```

## 3. 关键设计决策与理由

| 决策 | 理由 |
|---|---|
| Nginx 反代 + uvicorn 绑 127.0.0.1 | TLS/鉴权/超时交给 Nginx；应用不暴露公网，最小攻击面 |
| 直接 `uvicorn api.app:app`，不用 `run.py app` | `run.py app` 会 `webbrowser.open()` 且只绑 127.0.0.1，是桌面用法；`api.app:app` 自带调度器（`dev_app` 才是关掉调度器的） |
| 日本服务器直连、不配代理 | 国际源（yfinance/OKX/CNBC/Polymarket/Dune/CMC）在日本直连可达，彻底免掉本地 Clash；`config.py` 默认探测 `127.0.0.1:4780` 失败→自动直连 |
| systemd（非 Docker） | 单进程 Python + SQLite + 需访问 SFTP，systemd 最轻、最贴合；省掉容器内存开销与挂载配置 |
| Basic Auth + HTTPS | 应用零鉴权；Basic Auth 是单人仪表盘性价比最高的拦截，**必须**配合 HTTPS 否则密码明文 |
| 全新数据库 | 回补 ~72h 即可恢复监控；历史告警/标注非必需 |

## 3.5 数据源接入：本地文件后端（方案 B，需小代码改动）

已确认本机 co-tenant `coin-realtime-data_v1.1.11` 在本地产出与远程 BMAC **逐字一致**的数据：
`/home/ubuntu/data/firm/coin-realtime-data_v1.1.11/data/` 下
`preprocess_1h_resample/30m/market_pivot_{spot,swap}_<year>.pkl` + `market_pivot_{spot,swap}_<ts>.ready`、
`exginfo/spot_swap_matches.pkl` + `.ready`，全部 `644 root:root`（ubuntu 可读），每小时 `:30`(UTC) 更新。
前缀/结构与 `remote_puller.PHASE1_DATASETS` 完全匹配。故**弃用跨境 SFTP（`47.243.252.92`）+ 明文密码，改直接读本地盘**。

**改动范围（`services/remote_fs.py`）**：加 `REMOTE_BACKEND` 开关（`local` | `sftp`，默认 `sftp` 向后兼容）。`local` 模式只替换三个底层原语，签名不变：
- `list_dir(path)` → `os.scandir`（返回 `(name, size, mtime)`）
- `stat_remote(path)` → `os.stat`
- `pull(rel)` → `shutil.copy2(REMOTE_DATA_ROOT/rel → LOCAL_CACHE_DIR/...)`（保留原子写 + manifest 增量跳过，只换"取文件"动作）

**不改**：上层 `find_latest_ready` / `pull_many` / `load_pickle` / `load_pkl_as_df`（只调上述原语）；**不改** `remote_puller.PHASE1_DATASETS`（前缀已逐字匹配）。`local` 模式绝不调用 `_connect_kwargs()` / `_require_env("REMOTE_HOST")` → 无需 host/user/key/password。

**行为**：`remote_data_cycle` 每小时查 `.ready` cutoff，有新数据就把 pkl 拷进 `LOCAL_CACHE_DIR` 并触发 `sector_scan`。本地拷 ~70MB swap pkl 仅几十毫秒；manifest 的 mtime/size 校验保证只在源更新时才拷。

**测试（TDD）**：临时目录造 `market_pivot_*.{pkl,ready}` + `spot_swap_matches.*` fixture，验证 `local` 模式下 `find_latest_ready` 命中正确 cutoff、`pull` 落缓存、`load_pkl_as_df` 读回 DataFrame；并验证 `sftp` 默认模式行为不变（回归）。

**未来可优化（非本期）**：`local` 模式可改"原地读"省掉拷贝；v1 先保留拷贝以维持 manifest/原子写不变式。

## 4. 前置条件（Phase 0，部分可并行）

- [ ] **域名**：在任一注册商（Cloudflare / Namecheap / Porkbun / 腾讯云）注册。
      **服务器在日本，域名无需 ICP 备案。**
- [ ] **DNS**：加一条 `A` 记录 `<your-domain>` → CVM 公网 IP；`nslookup <your-domain>` 验证生效。
- [ ] **腾讯云安全组**：控制台为该 CVM 放行入站 **22 / 80 / 443**（最常见的坑：只配了系统 ufw，漏了云端安全组）。
- [ ] **密钥就绪**（见 §11 清单）。
- [ ] **BMAC 数据走本地后端（方案 B）**：不再使用跨境 SFTP（`47.243.252.92`）与那条泄露过的明文密码 → 该风险对本实例消除。公开仓库下任何密钥仍只放服务器 `.env`、绝不提交。

## 5. 部署阶段（含具体命令）

约定：仓库部署到 `/opt/market_monitor`，由现有 `ubuntu` 用户拥有（你登录的默认用户）。

### Phase 1 · 服务器初始化（root / sudo）

```bash
# 1) 部署目录（直接用你登录的 ubuntu 用户，无需新建用户；想要独立用户可自行 adduser deploy 再相应替换）
sudo install -d -o ubuntu -g ubuntu /opt/market_monitor

# 2) 时区：**不要改**。本机时区为 UTC+8 且跑着对时间敏感的 coin-realtime-data 采集器；
#    market_monitor 代码全程用显式 UTC（datetime.now(timezone.utc) / APScheduler timezone="UTC"），
#    不依赖系统时区，无需也不应改动系统时区。

# 3) swap：本机已有 ~5.9GB swap（`free -h` 实测），可用内存 2.7GB → 跳过此步。
#    若是干净机器无 swap，再执行：
# fallocate -l 2G /swapfile && chmod 600 /swapfile
# mkswap /swapfile && swapon /swapfile
# echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 4) 系统依赖
apt update
apt install -y python3 python3-venv python3-pip build-essential git \
               nginx apache2-utils certbot python3-certbot-nginx
# Node 20 LTS（构建前端）
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
```

### Phase 2 · 代码 + Python 环境 + 配置（以 `deploy` 身份）

```bash
# 以 ubuntu 身份执行（你登录的默认用户，已有 sudo）
git clone https://github.com/mashiro1988/market_monitor.git /opt/market_monitor
cd /opt/market_monitor

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
nano .env          # 按 §11 填密钥；PROXY_URL 保持注释（服务器直连）

.venv/bin/python run.py setup     # 初始化 SQLite 表
```

### Phase 3 · 构建前端

```bash
cd /opt/market_monitor/frontend
npm ci
npm run build      # 产出 ../frontend/dist（必须在启动服务前完成）
```

### Phase 4 · systemd 常驻（root / sudo）

写 `/etc/systemd/system/market-monitor.service`：

```ini
[Unit]
Description=Market Monitor (FastAPI + APScheduler)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/market_monitor
# 清理崩溃可能残留的扫描锁（见 §9 Linux 锁问题）
ExecStartPre=-/bin/rm -f /opt/market_monitor/.scan.lock
ExecStart=/opt/market_monitor/.venv/bin/uvicorn --app-dir /opt/market_monitor \
          api.app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
# 共享机器建议开启：限制本应用内存，超限被 OOM kill 后由 Restart 拉起，保护同机的 PM2 服务
MemoryMax=2G

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now market-monitor
journalctl -u market-monitor -f      # 观察启动 + 调度器日志
```

### Phase 5 · Nginx + Basic Auth + HTTPS（root / sudo）

```bash
# 1) Basic Auth 账号
htpasswd -c /etc/nginx/.htpasswd <your-username>   # 交互输入密码
```

写 `/etc/nginx/sites-available/market-monitor`（先 HTTP，certbot 再补 TLS）：

```nginx
server {
    listen 80;
    server_name <your-domain>;
    client_max_body_size 10m;

    location / {
        auth_basic "Market Monitor";
        auth_basic_user_file /etc/nginx/.htpasswd;

        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # 自动标注调 DeepSeek reasoner 最长 ~600s，避免 504
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
```

```bash
ln -sf /etc/nginx/sites-available/market-monitor /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# DNS 已生效、80 端口已放行后：签证书 + 自动配 443 与跳转
certbot --nginx -d <your-domain> --redirect -m <you@example.com> --agree-tos
```

> certbot 走 HTTP-01 校验，**需公网能访问 80 端口**（安全组 + ufw 都放行）且 DNS 已解析。
> 它会保留 location 内的 Basic Auth，并加上 443 server 块 + 80→443 跳转 + 续期定时器。

### Phase 6 · 防火墙（root / sudo）

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status
```
> 再次确认腾讯云控制台安全组同样放行 22/80/443（两层都要）。

### Phase 7 · 验证清单

- [ ] `curl -s -u user:pass https://<your-domain>/api/health | grep -q '"ok":true' && echo OK` → 打印 `OK`（响应体还含 timestamp，用包含匹配）
- [ ] 浏览器打开 → Basic Auth 弹窗 → 看到仪表盘
- [ ] `journalctl -u market-monitor -f`：5 分钟一次 `scan cycle`、`cmc_bootstrap`、`remote_data_cycle` 正常
- [ ] 「告警设置」页点「发送企业微信测试」→ 手机收到
- [ ] 等 1–2 个 5m 周期：市场/新闻/板块数据在增长
- [ ] `https://` 证书有效（小锁），HTTP 自动跳 HTTPS

## 6. 配置文件清单

- `/etc/systemd/system/market-monitor.service`（§5 Phase 4）
- `/etc/nginx/sites-available/market-monitor`（§5 Phase 5）
- `/opt/market_monitor/.env`（chmod 600，见 §11）
- `bootstrap.sh`（实现阶段产出，自动化 Phase 1–4）
- `deploy.sh`（更新用）：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/market_monitor
git pull --ff-only
.venv/bin/pip install -r requirements.txt
( cd frontend && npm ci && npm run build )        # 严格按 lockfile 安装并先构建
sudo systemctl restart market-monitor             # 再重启
sudo systemctl --no-pager status market-monitor
```

## 7. 安全加固

- `.env` `chmod 600`，仅 `ubuntu` 可读。公开仓库下密钥只在服务器 `.env`，绝不提交。
- Basic Auth 必须在 HTTPS 之上（已满足）。
- **BMAC 改本地读盘后无需任何凭据**：方案 B 不再连 `47.243.252.92`，那条泄露过的明文密码风险对本实例已消除（停用即可）。
- CVM 自身 SSH 建议：禁 root 密码登录、改密钥、可选 `fail2ban` 防爆破。

## 8. 运维

- **更新**：`deploy.sh`（git pull → pip → 构建前端 → 重启）。
- **备份**：备份目录不会自动建，先 `mkdir -p /opt/market_monitor/backup`；
  再定期 `sqlite3 market_monitor.db ".backup '/opt/market_monitor/backup/mm-$(date +%F).db'"`（WAL 安全，会自动 checkpoint，**别**改成 `cp`），含告警日志与人工标注。
- **日志**：`journalctl -u market-monitor`（应用 stdout→journald）。
- **证书**：certbot 自带 `systemd timer` 自动续期，无需手动。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| **Linux 锁卡死**：`run.py` 的 `_process_exists` 用 Windows 专有 `ctypes.windll`，Linux 上恒判"存活"。进程被硬杀（OOM/kill -9）残留 `.scan.lock` 会卡死扫描 | systemd `ExecStartPre=-/bin/rm -f .scan.lock` 每次启动前清锁，**已足够覆盖崩溃-重启**场景（正常异常路径有 `finally` 释放锁）。**可选**：把 `_process_exists` 改成跨平台 `os.kill(pid,0)` 顺手修掉根因 |
| 误开多 worker → 重复扫描/告警 | systemd unit 固定单进程、不加 `--workers`；本文 §1 明确警告 |
| 4GB 内存峰值（构建/加载 pivot） | 2GB swap +（可选）`MemoryMax=3G` |
| certbot 失败 | 多因 80 未放行或 DNS 未生效；先过 Phase 0/6 再签 |
| 国内访问日本 HTTPS 偶发抖动 | 正常直连可用；若明显丢包可选上 Cloudflare（隐藏真实 IP + 边缘 HTTPS） |
| 东方财富/SFTP 跨境延迟 | 非致命：失败仅对应数据项空缺，应用照常运行 |

## 10. 需要用户提供/确认的输入（§11）

`.env` 需填（值来自本地，**只在服务器写入，不提交**）：

```bash
# 直连：PROXY_URL 保持注释/留空即可
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_REASONER_MODEL=deepseek-v4-pro
WECHAT_WORK_WEBHOOK=
DUNE_API_KEY=
DUNE_QUERY_ID_ETH_TOP100_NETFLOW=
DUNE_QUERY_ID_ETH_DAILY_STATS=
DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT=
CMC_API_KEY=
# CMC_USE_PROXY=0          # 可选，默认 0（直连）；服务器无代理保持 0 即可
# 数据源 = 本地文件后端（方案 B）：直接读同机 coin-realtime-data 产出，无 SSH/密码
REMOTE_BACKEND=local
REMOTE_DATA_ROOT=/home/ubuntu/data/firm/coin-realtime-data_v1.1.11/data/
REMOTE_OFFSET=30m
REMOTE_PULLER_POLL_SECONDS=3600
# 已弃用跨境 SFTP（不再需要）：REMOTE_HOST / REMOTE_PORT / REMOTE_USER / REMOTE_PASSWORD / REMOTE_KEY_PATH
DATABASE_URL=sqlite:///market_monitor.db
```

其它部署期需要的值：域名、Basic Auth 用户名/密码、certbot 邮箱、CVM 公网 IP。
