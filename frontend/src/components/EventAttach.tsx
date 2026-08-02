import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";

/** 标注页 driver 新闻行的事件控件(news-research-phase1 spec §9.2):
 * 已挂事件 → 只读徽章 + 跳转研究页(接替旧主题下拉的位置);
 * isDriver 时给一个"挂到事件/新建"快捷下拉——价格已证明它重要,顺手立案。
 * 纠错类写操作(改归属/摘下)集中在研究页,这里只有快捷挂接。 */
export function EventAttach({ newsId, isDriver }: { newsId: number; isDriver: boolean }) {
  const qc = useQueryClient();
  const links = useQuery({ queryKey: ["research-news-links", newsId],
                           queryFn: () => api.researchNewsLinks(newsId),
                           staleTime: 60_000 });
  const events = useQuery({ queryKey: ["research-events", "active"],
                            queryFn: () => api.researchEvents({ status: "active" }),
                            staleTime: 60_000 });
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["research-news-links", newsId] });
    void qc.invalidateQueries({ queryKey: ["research-events"] });
  };
  const attach = useMutation({
    mutationFn: (eventId: number) => api.researchLinkCreate({ event_id: eventId, news_id: newsId }),
    onSuccess: invalidate,
  });
  const create = useMutation({
    mutationFn: (name: string) => api.researchEventCreate({
      name, news_ids: [newsId], created_from: "annotation" }),
    onSuccess: invalidate,
  });
  const linked = links.data?.items ?? [];
  return (
    <span style={{ display: "inline-flex", gap: 3, alignItems: "center", flexWrap: "wrap" }}>
      {linked.map((l) => (
        <Link key={l.link_id} to="/research" className="s-badge none"
              title="已挂事件,点击去研究页" style={{ textDecoration: "none" }}>
          #{l.event_id} {l.event_name}
        </Link>
      ))}
      {isDriver && (
        <select value="" title="挂到事件 / 新建(价格已证明它重要,顺手立案)"
                style={{ fontSize: 12, maxWidth: 96 }}
                onChange={(ev) => {
                  const v = ev.target.value;
                  if (v === "new") {
                    const name = window.prompt("新事件名(一个待重定价的变量;中文短名≤20字)", "");
                    if (name) create.mutate(name);
                  } else if (v) {
                    attach.mutate(Number(v));
                  }
                }}>
          <option value="">挂事件…</option>
          <option value="new">+ 新建…</option>
          {(events.data?.items ?? []).map((o) => (
            <option key={o.id} value={o.id}>#{o.id} {o.name}</option>
          ))}
        </select>
      )}
    </span>
  );
}
