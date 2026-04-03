# 📊 市场监控仪表板

基于Streamlit的自动化市场数据监控系统，可以实时跟踪股票指数、加密货币、债券利率等金融数据。

## ✨ 功能特性

- 📈 **股票指数监控**: 道琼斯、纳斯达克、标普500实时数据
- ₿ **加密货币监控**: BTC、ETH等主流币种价格跟踪
- 🏦 **债券利率监控**: 美国10年期、2年期国债收益率
- 📊 **数据可视化**: 交互式图表展示趋势
- 🔄 **自动化更新**: 定时获取最新数据
- 💾 **数据存储**: SQLite数据库持久化存储

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置API密钥（可选）

复制 `.env.example` 为 `.env` 文件，并填入您的API密钥：

```bash
# 经济数据API（可选，免费申请）
FRED_API_KEY=your_fred_api_key_here
```

> **注意**: FRED API密钥是可选的，主要用于获取CPI、失业率等经济数据。
> 申请地址: https://fred.stlouisfed.org/docs/api/api_key.html

### 3. 初始化数据库

```bash
python run.py setup
```

### 4. 采集初始数据

```bash
python run.py collect
```

### 5. 启动应用

```bash
python run.py app
```

应用将在浏览器中自动打开，访问地址: http://localhost:8501

## 📱 使用说明

### 主界面功能

1. **实时数据卡片**: 显示各指数当前价格和涨跌幅
2. **趋势图表**: 可视化显示价格走势
3. **控制面板**: 
   - 🔄 立即更新数据按钮
   - 显示/隐藏不同数据类型
   - 数据源说明
4. **详细数据表**: 可展开查看完整历史数据

### 数据更新

- **手动更新**: 点击侧边栏"立即更新数据"按钮
- **自动更新**: 修改 `config.py` 中的定时任务配置

## 🛠️ 技术架构

### 技术栈

- **前端**: Streamlit + Plotly
- **后端**: FastAPI + SQLAlchemy
- **数据库**: SQLite
- **数据源**: 
  - Yahoo Finance (yfinance)
  - Binance API (ccxt)
  - FRED API (fredapi)

### 项目结构

```
市场监控/
├── app.py              # Streamlit主应用
├── config.py           # 配置文件
├── database.py         # 数据库模型
├── data_collector.py   # 数据采集模块
├── run.py             # 启动脚本
├── requirements.txt    # 依赖包
├── README.md          # 说明文档
└── market_monitor.db  # SQLite数据库（自动生成）
```

## 📊 数据源说明

| 数据类型 | 数据源 | 更新频率 | API限制 |
|---------|--------|----------|---------|
| 美股指数 | Yahoo Finance | 实时 | 无限制 |
| 加密货币 | Binance | 实时 | 无限制 |
| 债券利率 | Yahoo Finance | 实时 | 无限制 |
| 经济数据 | FRED API | 日/月 | 需要密钥 |

## 🔧 自定义配置

### 添加新的数据源

修改 `config.py` 文件中的 `DATA_SOURCES` 配置：

```python
DATA_SOURCES = {
    "crypto_symbols": {
        "BTC": "BTC/USDT",
        "ETH": "ETH/USDT",
        "新币种": "SYMBOL/USDT"  # 添加新币种
    }
}
```

### 修改更新频率

在 `config.py` 中修改 `UPDATE_SCHEDULE`：

```python
UPDATE_SCHEDULE = {
    "crypto_data": "*/5 * * * *",  # 改为每5分钟更新
}
```

## 🚀 部署到云端

### 腾讯云部署（推荐）

1. 创建腾讯云 CloudBase 项目
2. 上传代码到云函数
3. 配置环境变量
4. 设置定时触发器

### 本地服务器部署

```bash
# 使用gunicorn部署（生产环境）
pip install gunicorn
gunicorn -w 4 -k uvicorn.workers.UvicornWorker app:app --bind 0.0.0.0:8000
```

## 🐛 常见问题

### Q: 数据更新失败？
A: 检查网络连接，确保能访问Yahoo Finance和Binance API

### Q: 图表不显示？
A: 确保已经采集了数据，点击"立即更新数据"按钮

### Q: 经济数据为空？
A: 需要配置FRED API密钥，或者该数据尚未发布

## 📈 未来规划

- [ ] 添加更多技术指标
- [ ] 支持自定义警报
- [ ] 移动端优化
- [ ] 数据导出功能
- [ ] 实时推送通知

## 📧 联系支持

如有问题或建议，请提交Issue或Pull Request。

---

**⚠️ 免责声明**: 本系统仅供学习和参考使用，所有数据仅用于展示目的，不构成投资建议。投资有风险，决策需谨慎。