# PaperTrail — Qoder 开发过程报告

## 1. 项目基本信息

- **项目名称：** PaperTrail — 科研文献检索工作台
- **比赛：** 2026 OceanBase 数据库大赛 Qoder 赛题
- **开发工具：** Qoder IDE、Qoder 桌面端及 Qoder Sites
- **核心技术：** Python、FastAPI、pyseekdb、seekdb、HTML/CSS/JavaScript
- **比赛分支：** `2026-competition-ecosystem`
- **代码目录：** `qoder/`
- **GitHub：** https://github.com/ssyyss12138/OceanBase-p/tree/2026-competition-ecosystem/qoder
- **项目网站：** https://papertrail-cd9ilylu9yc.qoder.zone

Qoder 账号身份核验材料见 `evidence/UID.png`。正式提交时应核对该账号与实际开发、Sites 发布账号的对应关系。

## 2. 项目需求与设计

科研人员和学生在文献阅读过程中，通常面临资料分散、检索不便和阅读进度难以统一管理的问题。

本项目通过 Qoder 开发一个科研文献管理工作台，将文献标题、作者、摘要、标签、个人笔记和阅读状态统一存储到 seekdb。

主要功能包括：

1. 文献新增、查看、编辑和删除。
2. 基于 seekdb 的关键词检索。
3. 按阅读状态筛选文献。
4. 文献数量和阅读进度统计。
5. 数据持久化及演示数据初始化。

## 3. Qoder 参与的主要开发过程

### 阶段一：需求拆解与环境准备

开发者向 Qoder IDE 提供项目需求、技术栈、功能范围和比赛约束，要求在指定 `qoder/` 目录中完成主要开发。

Qoder 将任务拆分为环境检查、seekdb SDK 验证、后端开发、前端开发、测试脚本和工程文档等步骤。

在环境准备阶段，Python 依赖安装曾遇到网络问题。开发者通过终端手动完成依赖安装，随后 Qoder 继续执行项目开发。

**证据：** `evidence/1.png`、`evidence/2.png`

### 阶段二：seekdb 数据层与核心功能开发

Qoder 研究 pyseekdb 的真实 API，并使用嵌入式 seekdb 实现数据管理。

核心技术实现包括：

- 使用 AdminClient 创建数据库。
- 使用 Client 和 Collection API 操作文献。
- 实现真实的数据写入、查询、更新和删除。
- 利用 `where_document` 实现关键词检索。
- 结合全文匹配与正则子串匹配改善中文检索结果。
- 构建 FastAPI REST 接口及原生 JavaScript 前端。

在开发过程中，Qoder 对全文检索的分词行为、停用字误命中和索引可见性进行了多轮排查，并根据实际测试结果调整查询逻辑。

核心文件：

- `app/seekdb_store.py`
- `app/main.py`
- `app/static/app.js`
- `app/static/index.html`
- `app/static/styles.css`

### 阶段三：测试与验证

Qoder 编写 `scripts/verify_seekdb.py`，对数据库核心功能进行验证。

实际测试结果：

| 验证项目 | 结果 |
|---|---|
| 数据库功能测试 | 27 项通过，0 项失败 |
| 跨进程持久化测试 | 9 项通过，0 项失败 |

Qoder 此外报告完成 12 项端到端 API 测试。数据库测试的独立截图和可复现步骤见《seekdb 技术验证报告》。

**证据：** `evidence/8.png`、`evidence/9.png`

### 阶段四：前端异常定位与修复

开发过程中发现：应用首次打开正常，但刷新后文献列表持续显示加载状态，统计卡片未能显示数据。

通过 API 检查确认，`/api/health`、`/api/papers` 和 `/api/stats` 均可正常响应。

Qoder 进一步分析服务器请求日志，发现页面首次加载只触发健康检查，没有触发文献和统计数据加载。

最终定位根因：

前端 `switchView()` 引用了 `paper-section` 元素，但 HTML 缺少对应的 ID，导致 JavaScript 抛出 TypeError，中断后续初始化流程。

Qoder 修复缺失的 HTML ID，并补充初始化异常处理、请求超时和加载失败提示。

修复后，Qoder 通过真实浏览器验证：刷新后正常显示 5 篇文献，其中已读 2 篇、未读 3 篇。

**证据：** `evidence/3.png`、`evidence/4.png`

### 阶段五：Qoder Sites 展示网站制作与发布

核心应用开发完成后，通过 Qoder 制作静态项目展示网站。

展示网站使用真实应用截图，包含项目介绍、解决的问题、核心功能、技术架构、验证结果、运行方式和 GitHub 链接。

随后通过 Qoder 桌面端的 Sites 功能完成构建、预览及公开发布。

**证据：** `evidence/6.png`、`evidence/7.png`

## 4. Qoder 开发实践总结

本项目中，Qoder 实际承担了主要开发工作，具体包括：

- 将自然语言需求转化为具体开发任务。
- 研究并接入 seekdb 的 Python SDK。
- 编写前后端核心业务代码。
- 构建数据库测试和验证脚本。
- 根据运行日志分析问题并执行修复。
- 验证主要功能。
- 参与展示网站创建与发布。

开发者负责需求约束、关键问题反馈、环境操作、实际功能验收及材料整理。

Qoder 的作用覆盖了从需求理解、编码实现、测试调试到交付展示的主要过程，而不是仅用于生成介绍文档或发布网站。

## 5. Git 提交记录

核心功能提交：

`27b4593` — `feat(qoder): implement PaperTrail with seekdb`

展示网站提交：

`7ca4794` — `docs(qoder): add PaperTrail showcase website`

上述提交均位于 `2026-competition-ecosystem` 比赛分支。

## 6. 开发过程证据索引

| 图片 | 内容 |
|---|---|
| 1.png | Qoder 开发操作与 SDK 研究 |
| 2.png | 需求指令及项目开发任务 |
| 3.png | 前端问题反馈与定位 |
| 4.png | 修复完成及真实浏览器验证 |
| 5.png | 开发与网站准备的补充记录 |
| 6.png | Qoder Sites 文件及截图导入 |
| 7.png | Sites 构建、预览与发布 |
| 8.png | seekdb 功能验证 |
| 9.png | seekdb 跨进程持久化验证 |
| UID.png | Qoder 账号身份核验 |

所有截图均来自实际操作过程，保留原始界面及执行结果。

## 7. 项目交付

项目已提供完整源代码、依赖清单、启动说明、验证脚本、真实功能截图和 Qoder Sites 公开展示网址。

核心应用采用本地 seekdb 嵌入式数据库运行，Qoder Sites 用于公开展示项目，不将静态展示页面冒充在线数据库服务。

评审可根据 README 在本地复现主要功能，并使用验证脚本核对 seekdb 的真实技术作用。