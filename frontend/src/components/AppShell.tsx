import { NavLink, Outlet } from "react-router-dom";
import { Activity, Bell, Boxes, Coins, Crosshair, FolderSearch, Grid, LineChart, Newspaper, PenSquare } from "lucide-react";

const navItems = [
  { to: "/market", label: "市场概览", icon: LineChart },
  { to: "/strategy", label: "持仓策略", icon: Crosshair },
  { to: "/sectors", label: "板块轮动", icon: Grid },
  { to: "/news", label: "新闻快讯", icon: Newspaper },
  { to: "/crypto-news", label: "加密快讯", icon: Coins },
  { to: "/alerts", label: "告警设置", icon: Bell },
  { to: "/annotations", label: "新闻标注", icon: PenSquare },
  { to: "/behavior", label: "行为面板", icon: Activity },
  { to: "/research", label: "宏观事件池", icon: FolderSearch },
  { to: "/crypto-research", label: "加密事件池", icon: Boxes }
];

export function AppShell() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Activity size={22} />
          <div>
            <strong>Market Monitor</strong>
            <span>本地交易台</span>
          </div>
        </div>
        <nav>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink key={item.to} to={item.to} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
                <Icon size={18} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
      </aside>
      <main className="main-pane">
        <Outlet />
      </main>
    </div>
  );
}
