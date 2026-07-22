# deploy.sh 部署前备份改造：VACUUM INTO + 完整性校验 + 保留策略

日期：2026-07-22　状态：已由用户批准（方案 A / N=10 / 存量自然滚掉）

## 1. 背景与证据

- `deploy.sh` 的 `backup_sqlite()` 在 `systemctl restart` 之前执行，此时 market-monitor
  服务仍在运行、每 5 分钟写库（WAL 模式，-wal 常驻 ~42MB）。现行实现用
  `sqlite3.Connection.backup()` 拷贝 301MB 活库，且**从不校验产物**。
- 2026-07-22 实证（远程数据访问工作流，同一台服务器、同一个库）：活跃写入下
  `backup()` 产出"混时态"损坏快照——`PRAGMA integrity_check` 报 freelist 少 221 页、
  树页双引用；同条件下 `VACUUM INTO` 产物干净。结论：`backup()` 在该负载下会
  **概率性**产出坏文件（撞上写入竞态才坏）。
- 2026-07-22 对服务器 `backups/` 抽查 5 份（07-09 最早、07-16、07-21×2、07-22 最新）
  `integrity_check` 全部通过：部署备份**未**"全坏"，但坏文件一旦产生要等到恢复时才被发现。
- 容量：13 天累计 43 份 × 301MB = 13GB，盘 59G 已用 69%（剩 18G）；高频部署日
  （07-10 单日 10 次）一天新增 3GB。无保留策略，几周内可写满盘。

## 2. 决策（用户已拍板）

重写 `backup_sqlite()`，行为如下：

1. **只读打开活库**：`file:<db>?mode=ro` URI（现行代码以读写模式打开，顺带消除）；
   `PRAGMA busy_timeout=30000` 兜底。
2. **`VACUUM INTO`** 到 `backups/market_monitor_<ts>.db`（文件名模式与存量一致，
   保留策略统一覆盖新旧文件）。WAL 下为普通读事务，不阻塞写入；SQLite ≥3.27。
3. **产物校验**：`PRAGMA integrity_check` ≠ `ok` → 产物改名加 `.corrupt` 后缀留现场
   （不匹配保留策略的 `*.db` glob，不会被误当好备份），`exit 1`。脚本已有
   `set -euo pipefail`，部署在 `git pull` 之前中止。
4. **保留策略**：成功校验后按 mtime 保留最近 N 份（默认 10 ≈ 3GB），删除更旧的
   `market_monitor_*.db`。N 由 `MARKET_MONITOR_DB_BACKUP_KEEP` 覆盖。
   **首跑效应**：下次部署先新增 1 份再保留 10 份，一次性滚掉约 34 份存量、
   释放约 10GB（用户已确认接受）。

预期成本：VACUUM INTO 约 30–60s + 校验约 15s（实测 301MB 校验 7–16s），
每次部署加时约 1 分钟。

## 3. 备选方案（已否决）

- B 停服冷拷贝：绝对一致，但每次部署 +30–60s 停机且需重排重启逻辑，对单日 10 次
  部署不友好。
- C 保留 `backup()` 仅加校验：改动最小，但产坏文件的根源还在，坏了就随机中止部署。

## 4. 配套文档修改

- `docs/superpowers/specs/2026-07-22-yfinance-throttle-and-backfill-design.md` §3.2：
  导入前快照仍写着"现成 `sqlite3.Connection.backup()` 在线备份流程"（尚未实施），
  改为 VACUUM INTO + integrity_check，避免复刻风险。
- `docs/specs/deployment.md` §6：如有描述备份行为的句子同步更新。

## 5. 验收标准

- `bash -n deploy.sh` 通过；备份 python 片段在本地对临时库跑通（产物校验 ok）。
- 保留策略本地干跑：12 个假文件 → 恰好保留最新 10 个。
- 服务器彩排：以与脚本完全相同的 python 片段对活库 VACUUM INTO 至 /tmp 临时文件，
  integrity_check 返回 ok，记录耗时后删除临时文件。
- 上线后首次真实部署：备份产出并通过校验、存量滚到 10 份（此项在下次部署时人工确认）。

## 6. 回滚

`git revert` 本次提交即回到旧 `backup()` 行为（不推荐；仅当 VACUUM INTO 在生产上
出现未预见问题时应急用）。
