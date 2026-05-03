import { Database } from "lucide-react";
import { PageHeader } from "../components/Controls";

export function OnchainPage() {
  return (
    <section>
      <PageHeader title="链上数据" subtitle="Dune 查询接口已保留，交互页暂不展开" />
      <div className="onchain-placeholder">
        <Database size={42} />
        <h2>链上数据导航已保留</h2>
        <p>后端提供 ETH Top100 净买入、每日统计、CEX 资金流三个 Dune REST 接口，并使用 60 分钟缓存。当前版本先把交易台主流程迁移完成，链上交互页保持占位。</p>
        <div className="endpoint-list">
          <code>/api/onchain/eth/top100-netflow</code>
          <code>/api/onchain/eth/daily-stats</code>
          <code>/api/onchain/eth/cex-flows</code>
        </div>
      </div>
    </section>
  );
}
