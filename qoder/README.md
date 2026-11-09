# PaperTrail 科研文献检索工作台

基于 **FastAPI + seekdb**(OceanBase 衍生向量/全文检索数据库)的科研文献管理应用,
支持文献增删改查、关键词检索与阅读状态管理,前端为原生 HTML/CSS/JS 中文 Dashboard。

- 数据库:seekdb 嵌入式模式(官方 `pyseekdb` SDK,`Client(path=..., database=...)`),
  数据落盘于本地目录,无外部服务依赖、无付费 LLM API
- 检索:seekdb 全文索引(IK 分词) + 正则子串兜底,真实数据库检索,非前端模拟
- 后端:Python 3.11+ / FastAPI / uvicorn
- 前端:原生 HTML/CSS/JS,中文浅色 Dashboard(侧边导航、统计卡片、文献列表、搜索、详情/编辑弹窗)

## 目录结构

```
qoder/
├── app/
│   ├── main.py              # FastAPI 应用与 REST API
│   ├── seekdb_store.py      # seekdb 存储层(初始化/增删改查/检索/统计/幂等演示数据)
│   └── static/              # 前端页面(index.html / styles.css / app.js)
├── scripts/
│   └── verify_seekdb.py     # seekdb 真实读写验证脚本(两阶段,含跨进程持久化校验)
├── data/                    # 运行时数据目录(seekdb 数据库文件,已被 .gitignore 忽略)
├── requirements.txt
└── .gitignore
```

## 安装与启动

依赖:Python 3.11+(`pyseekdb` 要求 3.11 及以上)。

```bash
cd qoder
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 启动应用
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 <http://127.0.0.1:8000>。

首次启动自动初始化 seekdb 数据库并幂等写入 3 条演示文献;
数据持久化在 `data/seekdb.db/`,重启应用后数据保留。
可用环境变量 `PAPERTRAIL_DATA_DIR` 指定其他数据目录。

## 功能

- **文献管理**:新增/查看/编辑/删除文献,字段包括标题、作者、摘要、链接、标签、阅读状态(已读/未读)、个人笔记
- **关键词检索**:对标题/摘要/笔记等做 seekdb 全文检索,可叠加阅读状态筛选
- **工作台统计**:文献总数、已读、未读、最近添加
- **演示数据**:3 条代表性文献,`POST /api/init` 或点击页面「初始化演示数据」可幂等重建,不产生重复

## REST API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/stats` | 统计(总数/已读/未读/最近添加) |
| GET | `/api/papers?q=&status=&limit=&offset=` | 列表与检索 |
| GET | `/api/papers/{id}` | 文献详情 |
| POST | `/api/papers` | 新增文献 |
| PUT | `/api/papers/{id}` | 更新文献 |
| DELETE | `/api/papers/{id}` | 删除文献 |
| POST | `/api/init` | 幂等写入演示文献 |

## 验证

```bash
cd qoder
.venv/bin/python scripts/verify_seekdb.py --write   # 写入/增删改查/统计/幂等初始化(27 项断言)
.venv/bin/python scripts/verify_seekdb.py --verify  # 新进程重开数据库,校验跨进程持久化(9 项断言)
```

两个阶段分别在独立进程运行,以真实证明数据已持久化到 seekdb;
全部断言通过时退出码为 0。

## 实现说明

- **seekdb 嵌入式模式**:`AdminClient(path)` 创建数据库后,`Client(path, database)` 打开集合;
  集合创建时显式传入 `embedding_function=None`,不下载任何嵌入模型(离线可用),
  写入/更新时提供定长占位向量满足向量列约束,应用不使用向量检索能力
- **中文检索适配**:seekdb 默认 IK 分词器可能把复合词合并为单个词元导致短关键词漏检,
  检索条件采用全文匹配(`$contains`,先剔除常见停用字)与原始关键词子串匹配(`$regex`)取并集,
  兼顾分词召回与精确子串命中
- **索引可见性**:seekdb 全文索引异步构建,写操作后轮询等待索引可见再返回,
  保证「写入后立即可检索」;删除后同样刷新索引
- **初始化健壮性**:嵌入式引擎启动/退出存在短暂锁窗口,初始化失败自动重试,
  并清理上次进程遗留的 `run/seekdb.pid` 等运行时文件
