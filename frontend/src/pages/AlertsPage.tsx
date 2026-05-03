import { useMutation, useQuery } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { api } from "../api/client";
import type { AlertLog, AlertRule } from "../api/types";
import { Button, PageHeader, SelectControl } from "../components/Controls";
import { DataTable } from "../components/DataTable";
import { ErrorState, LoadingState } from "../components/StateViews";
import { useState } from "react";

export function AlertsPage() {
  const [hours, setHours] = useState("24");
  const rules = useQuery({ queryKey: ["alert-rules"], queryFn: api.alertRules });
  const webhook = useQuery({ queryKey: ["webhook-status"], queryFn: api.webhookStatus });
  const logs = useQuery({ queryKey: ["alert-logs", hours], queryFn: () => api.alertLogs({ hours_back: Number(hours), page_size: 100 }) });
  const test = useMutation({ mutationFn: api.testWechat });

  return (
    <section>
      <PageHeader
        title="告警设置"
        subtitle={webhook.data?.configured ? `Webhook 已配置：${webhook.data.preview}` : "企业微信 Webhook 未配置"}
        actions={<Button onClick={() => test.mutate()} disabled={test.isPending}><Send size={16} />发送测试</Button>}
      />
      {test.data ? <div className={`task-banner ${test.data.ok ? "succeeded" : "failed"}`}>{test.data.message}</div> : null}
      {test.error ? <ErrorState error={test.error} /> : null}

      <section className="panel">
        <div className="panel-head"><h2>当前规则</h2></div>
        {rules.isLoading ? <LoadingState /> : rules.error ? <ErrorState error={rules.error} /> : (
          <DataTable<AlertRule>
            rows={rules.data ?? []}
            columns={[
              { key: "name", header: "名称", cell: (row) => row.name },
              { key: "type", header: "类型", cell: (row) => row.rule_type },
              { key: "params", header: "参数", cell: (row) => <code>{JSON.stringify(row.params)}</code> },
              { key: "channels", header: "通道", cell: (row) => row.channels.join(", ") },
              { key: "cooldown", header: "冷却", cell: (row) => `${row.cooldown_minutes}m` },
              { key: "enabled", header: "启用", cell: (row) => row.enabled ? "是" : "否" }
            ]}
          />
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>发送历史</h2>
          <SelectControl label="回溯" value={hours} onChange={setHours} options={[
            { label: "24小时", value: "24" },
            { label: "7天", value: "168" }
          ]} />
        </div>
        {logs.isLoading ? <LoadingState /> : logs.error ? <ErrorState error={logs.error} /> : (
          <DataTable<AlertLog>
            rows={logs.data?.items ?? []}
            columns={[
              { key: "time", header: "北京时间", cell: (row) => row.timestamp_bj },
              { key: "rule", header: "规则", cell: (row) => row.rule_name },
              { key: "channel", header: "通道", cell: (row) => row.channel },
              { key: "delivered", header: "送达", cell: (row) => row.delivered ? "是" : "否" },
              { key: "message", header: "消息", cell: (row) => row.message.slice(0, 180) }
            ]}
          />
        )}
      </section>
    </section>
  );
}
