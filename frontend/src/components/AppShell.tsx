import { NavLink, Outlet } from "react-router-dom";
import { Activity, Bell, Grid, LineChart, Newspaper, PenSquare, Radar } from "lucide-react";

const navItems = [
  { to: "/market", label: "市场概览", icon: LineChart },
  { to: "/sectors", label: "板块轮动", icon: Grid },
  { to: "/news", label: "新闻快讯", icon: Newspaper },
  { to: "/predictions", label: "预测市场", icon: Radar },
  { to: "/alerts", label: "告警设置", icon: Bell },
  { to: "/annotations", label: "新闻标注", icon: PenSquare },
  { to: "/behavior", label: "行为面板", icon: Activity }
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
