"""
告警设置页 - 告警规则管理、webhook 测试、告警历史
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from database import get_session
from models.alert_log import AlertLog
import config

st.title("告警设置")

# ── 企业微信 Webhook 状态 ──
st.markdown("### 企业微信机器人")
webhook = config.WECHAT_WORK_WEBHOOK
if webhook:
    st.success(f"Webhook 已配置: {webhook[:50]}...")

    if st.button("发送测试消息"):
        from alerts.channels.wechat_work import WeChatWorkChannel
        ch = WeChatWorkChannel()
        ok = ch.send("测试消息", "Investment Agent 告警系统测试。如果你看到此消息，说明 Webhook 配置正确。")
        if ok:
            st.success("测试消息发送成功！")
        else:
            st.error("发送失败，请检查 Webhook URL")
else:
    st.warning("企业微信 Webhook 未配置。请在 `.env` 文件中设置 `WECHAT_WORK_WEBHOOK`。")

st.markdown("---")

# ── 当前告警规则 ──
st.markdown("### 当前告警规则")

rules_data = []
for rule in config.ALERT_RULES:
    rules_data.append({
        "名称": rule["name"],
        "类型": rule["rule_type"],
        "参数": str(rule.get("params", {})),
        "通道": ", ".join(rule.get("channels", [])),
        "冷却(分钟)": rule.get("cooldown_minutes", 30),
        "启用": "是" if rule.get("enabled", True) else "否",
    })

if rules_data:
    st.dataframe(pd.DataFrame(rules_data), use_container_width=True)
else:
    st.info("暂无告警规则配置")

st.caption("规则配置在 `config.py` 的 `ALERT_RULES` 中修改。后续版本将支持 UI 动态配置。")

st.markdown("---")

# ── 告警历史 ──
st.markdown("### 告警发送历史")

hours_back = st.slider("回溯时长(小时)", 1, 168, 24, key="alert_hours")


@st.cache_data(ttl=60)
def load_alert_logs(hours: int):
    session = get_session()
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        logs = session.query(AlertLog).filter(
            AlertLog.timestamp >= cutoff,
        ).order_by(AlertLog.timestamp.desc()).limit(200).all()

        return [{
            "时间": log.timestamp,
            "规则": log.rule_name,
            "通道": log.channel,
            "已送达": "是" if log.delivered else "否",
            "消息": log.message[:200],
        } for log in logs]
    finally:
        session.close()


logs = load_alert_logs(hours_back)

if logs:
    st.dataframe(pd.DataFrame(logs), use_container_width=True)
else:
    st.info(f"过去 {hours_back} 小时内无告警记录")
