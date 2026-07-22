# deploy.sh 备份改造（VACUUM INTO + 校验 + 保留策略）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `deploy.sh` 的 `backup_sqlite()` 从活跃写入下会概率性产坏快照的 `sqlite3.Connection.backup()` 换成 `VACUUM INTO` + `PRAGMA integrity_check`（失败中止部署）+ 保留最近 10 份，并同步两处文档。

**Architecture:** 单函数重写：只读 URI 打开活库 → `VACUUM INTO` 产快照 → 校验产物，任何失败把产物改名 `.corrupt` 留现场并以非零退出（脚本 `set -euo pipefail` 保证部署在 `git pull` 前中止）→ 校验通过后按 mtime 保留最近 N 份。测试用"从 deploy.sh 提取真实函数体执行"的本地沙箱 harness（不复制逻辑），最后在服务器上用同一 python 片段对活库彩排。

**Tech Stack:** bash（deploy.sh，服务器 Ubuntu）、python stdlib sqlite3（服务器 `.venv/bin/python`）、本地 Git Bash + `D:\anaconda\python.exe`。

**Spec:** `docs/superpowers/specs/2026-07-22-deploy-backup-vacuum-into-design.md`

---

### Task 1: 本地测试 harness（先行，RED）

**Files:**
- Create: `C:\Users\Lenovo\AppData\Local\Temp\claude\D--market-monitor--claude-worktrees-recursing-haslett-4b017b\0ba559e5-4c08-4ecc-aeed-43867f9a00d6\scratchpad\test_backup_sqlite.sh`（临时测试，不进仓库）

- [ ] **Step 1: 写测试脚本**（提取 deploy.sh 里真实的 `backup_sqlite` 函数体执行；用例 1 = 正常备份+校验+保留 10 份，用例 2 = 损坏源库 → 非零退出且 backups/ 无 `.db` 残留）

```bash
#!/usr/bin/env bash
# 本地测试 deploy.sh 的 backup_sqlite()：awk 提取脚本中真实函数体执行，不复制逻辑。
set -euo pipefail

REPO="${1:?usage: test_backup_sqlite.sh <repo_dir>}"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
cd "$SANDBOX"

# anaconda python shim，模拟服务器的 .venv/bin/python
mkdir -p .venv/bin
printf '#!/bin/sh\nexec /d/anaconda/python.exe "$@"\n' > .venv/bin/python
chmod +x .venv/bin/python

# 造一个有数据的源库
.venv/bin/python - <<'MKDB'
import sqlite3
con = sqlite3.connect("test.db")
con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
con.executemany("INSERT INTO t (v) VALUES (?)", [("x" * 100,) for _ in range(1000)])
con.commit()
con.close()
MKDB

# 11 份假存量备份，mtime 按日期递增
mkdir -p backups
for i in $(seq -w 1 11); do
  f="backups/market_monitor_202607${i}T000000Z.db"
  echo dummy > "$f"
  touch -d "2026-07-${i#0} 00:00:00" "$f"
done

# 提取 deploy.sh 中真实的函数定义（函数起始行到首个顶格 } 为止）
eval "$(awk '/^backup_sqlite\(\)/,/^}$/' "$REPO/deploy.sh")"

# 用例 1：正常备份 → 产物可读、校验通过、恰保留 10 份、最旧的被删
MARKET_MONITOR_DB_PATH=test.db MARKET_MONITOR_DB_BACKUP_DIR=backups \
  MARKET_MONITOR_DB_BACKUP_KEEP=10 backup_sqlite
count=$(ls -1 backups/market_monitor_*.db | wc -l)
[[ "$count" -eq 10 ]] || { echo "FAIL: 保留 $count 份，应为 10"; exit 1; }
if ls backups/market_monitor_*.db 2>/dev/null | grep -q 20260701; then
  echo "FAIL: 最旧假备份 20260701 未被删除"; exit 1
fi
newest=$(ls -1t backups/market_monitor_*.db | head -1)
.venv/bin/python -c "import sqlite3; con = sqlite3.connect('$newest'); n = con.execute('select count(*) from t').fetchone()[0]; assert n == 1000, n; print('用例1 PASS: 新备份可读、行数一致、恰保留 10 份')"

# 用例 2：损坏源库 → 非零退出，backups/ 不新增 .db（失败产物只允许以 .corrupt 留下）
head -c 300 /dev/urandom > garbage.db
before=$(ls -1 backups/market_monitor_*.db | wc -l)
if MARKET_MONITOR_DB_PATH=garbage.db MARKET_MONITOR_DB_BACKUP_DIR=backups \
   MARKET_MONITOR_DB_BACKUP_KEEP=10 backup_sqlite 2>/dev/null; then
  echo "FAIL: 损坏源库应导致非零退出"; exit 1
fi
after=$(ls -1 backups/market_monitor_*.db | wc -l)
[[ "$before" -eq "$after" ]] || { echo "FAIL: 失败路径在 backups/ 留下了 .db 文件"; exit 1; }
echo "用例2 PASS: 损坏源库 → 非零退出，无 .db 残留"
echo "ALL PASS"
```

- [ ] **Step 2: 对当前 deploy.sh 运行，确认 RED**

Run: `bash <scratchpad>/test_backup_sqlite.sh <repo_dir>`
Expected: FAIL —— 现行函数无保留策略，用例 1 数出 12 份 ≠ 10（现行 `backup()` 对损坏源库同样会栈错误退出，用例 2 可能恰好通过，不影响 RED 结论）。

### Task 2: 重写 `backup_sqlite()`（GREEN）

**Files:**
- Modify: `deploy.sh:7-33`（整个 `backup_sqlite()` 函数体）

- [ ] **Step 1: 用下面内容整体替换 7–33 行的函数**

```bash
backup_sqlite() {
  local db_path="${MARKET_MONITOR_DB_PATH:-market_monitor.db}"
  if [[ ! -f "$db_path" ]]; then
    echo "SQLite 数据库不存在，跳过备份: $db_path"
    return
  fi

  local backup_dir="${MARKET_MONITOR_DB_BACKUP_DIR:-backups}"
  local keep="${MARKET_MONITOR_DB_BACKUP_KEEP:-10}"
  local ts
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$backup_dir"

  local py=".venv/bin/python"
  if [[ ! -x "$py" ]]; then
    py="python3"
  fi

  # 活跃写入下 sqlite3.Connection.backup() 会概率性产出"混时态"损坏快照（2026-07-22
  # 实证，见 docs/superpowers/specs/2026-07-22-deploy-backup-vacuum-into-design.md），
  # 改用 VACUUM INTO 并校验产物；失败则把产物改名 .corrupt 留现场、非零退出，
  # set -e 使部署在 git pull 之前中止。
  "$py" - "$db_path" "$backup_dir/market_monitor_${ts}.db" <<'PY'
import os
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
try:
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("VACUUM INTO ?", (dst,))
    con.close()
    chk = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    result = [row[0] for row in chk.execute("PRAGMA integrity_check")]
    chk.close()
    if result != ["ok"]:
        raise RuntimeError(f"integrity_check: {result[:5]}")
except Exception:
    if os.path.exists(dst):
        os.replace(dst, dst + ".corrupt")
    raise
print(f"SQLite backup written & verified: {dst} ({os.path.getsize(dst) / 1e6:.0f} MB)")
PY

  # 校验通过后按 mtime 保留最近 keep 份；上面刚写入一份，glob 必有匹配（pipefail 安全），
  # .corrupt 后缀不匹配 *.db，不会被误删也不会被误当好备份。
  ls -1t "$backup_dir"/market_monitor_*.db | tail -n +$((keep + 1)) | xargs -r rm --
}
```

- [ ] **Step 2: 语法检查 + 跑 harness，确认 GREEN**

Run: `bash -n deploy.sh && bash <scratchpad>/test_backup_sqlite.sh <repo_dir>`
Expected: `用例1 PASS` + `用例2 PASS` + `ALL PASS`。若 `VACUUM INTO ?` 参数绑定报错（老 SQLite 不支持绑定时的备选），改为拼接单引号转义字面量：`con.execute("VACUUM INTO '%s'" % dst.replace("'", "''"))`，重跑至 PASS。

- [ ] **Step 3: Commit**

```bash
git add deploy.sh
git commit -m "fix(deploy): backup via VACUUM INTO + integrity verify + keep-10 retention"
```

### Task 3: 同步两处文档

**Files:**
- Modify: `docs/superpowers/specs/2026-07-22-yfinance-throttle-and-backfill-design.md:50-51`
- Modify: `docs/specs/deployment.md:282-283`

- [ ] **Step 1: yfinance 设计稿 §3.2 —— 把**

```
- **写库前置动作：线上库快照备份**（现成 `sqlite3.Connection.backup()` 在线备份流程，
  备份文件服务器 `/tmp` 与本地各留一份）。这是对生产库写操作的后悔药。
```

**替换为**

```
- **写库前置动作：线上库快照备份**（用 `VACUUM INTO` 产快照并跑 `PRAGMA integrity_check`
  校验；活跃写入下 `sqlite3.Connection.backup()` 会概率性产损坏快照，2026-07-22 实证，
  见 2026-07-22-deploy-backup-vacuum-into-design.md。备份文件服务器 `/tmp` 与本地
  各留一份）。这是对生产库写操作的后悔药。
```

- [ ] **Step 2: deployment.md §6 —— 把**

```
- **备份**：备份目录不会自动建，先 `mkdir -p /opt/market_monitor/backup`；
  再定期 `sqlite3 market_monitor.db ".backup '/opt/market_monitor/backup/mm-$(date +%F).db'"`（WAL 安全，会自动 checkpoint，**别**改成 `cp`），含告警日志与人工标注。
```

**替换为**

```
- **备份**：`deploy.sh` 每次部署前自动 `VACUUM INTO` 快照到 `/opt/market_monitor/backups/`
  并跑 `PRAGMA integrity_check`（失败即中止部署），默认保留最近 10 份（约 3GB，
  `MARKET_MONITOR_DB_BACKUP_KEEP` 可覆盖）。如需手动快照同样用 `VACUUM INTO`：
  活跃写入下 `sqlite3.Connection.backup()` / CLI `.backup` 会概率性产出损坏快照
  （2026-07-22 实证，见 superpowers/specs 同日设计稿），**也别**用 `cp`。
  快照含告警日志与人工标注。
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-22-yfinance-throttle-and-backfill-design.md docs/specs/deployment.md
git commit -m "docs: backup guidance switched to VACUUM INTO (backup()/.backup deprecated)"
```

### Task 4: 服务器彩排（真实活库 + 真实解释器）

**Files:** 无仓库改动；临时文件 `<scratchpad>/snippet.py` 与服务器 `/tmp/mm_rehearsal.db`

- [ ] **Step 1: 从 deploy.sh 提取 heredoc python 片段**（保证彩排代码与上线代码逐字节一致）

```bash
awk "/<<'PY'/{f=1;next} /^PY$/{f=0} f" deploy.sh > <scratchpad>/snippet.py
```

- [ ] **Step 2: 在服务器对活库执行，计时**

```bash
time ssh mmon "cd /opt/market_monitor && nice -n 10 .venv/bin/python - market_monitor.db /tmp/mm_rehearsal.db" < <scratchpad>/snippet.py
```

Expected: `SQLite backup written & verified: /tmp/mm_rehearsal.db (~300 MB)`，总耗时 < 3 分钟（预估 30–90s）。若报 `/tmp/mm_rehearsal.db` 已存在，先 `ssh mmon "rm -f /tmp/mm_rehearsal.db*"` 再跑。

- [ ] **Step 3: 清理彩排产物**

```bash
ssh mmon "rm -f /tmp/mm_rehearsal.db /tmp/mm_rehearsal.db.corrupt"
```

- [ ] **Step 4: 勾掉计划复选框并最终报告**（抽查结论、RED→GREEN 证据、彩排耗时、上线路径：merge 到 main → 服务器下次 `./deploy.sh` 生效并首跑滚存量）

## Self-Review 结论

- Spec 覆盖：§2.1–2.4（mode=ro/busy_timeout、VACUUM INTO、.corrupt+中止、保留 N）→ Task 2；§4 两处文档 → Task 3；§5 验收（bash -n、本地跑通、保留干跑、服务器彩排）→ Task 1/2/4。首次真实部署的确认（§5 末条）留待用户下次部署，计划内无法覆盖，已在最终报告步骤注明。
- 无占位符；函数名、env 变量名（`MARKET_MONITOR_DB_BACKUP_KEEP`）、文件名模式在各 Task 间一致。
- 用例 2 兼测"失败产物不污染 backups/"，对应实现里 except 分支的 `.corrupt` 改名。
