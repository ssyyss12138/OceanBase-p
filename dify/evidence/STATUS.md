# 验证状态

本文件用于明确区分已经实测与尚未实测的能力，后续完成联调后更新。

| 项目 | 状态与证据 |
| --- | --- |
| Python 规则、三结局、重复结算、版本冲突与恢复日志 | 13 项测试通过，`tests/` |
| 真实 PowerContext Source / Memory 写入与 Context 读取 | 已通过，`powercontext-integration.json` |
| 玩家隔离、跨会话式读取与业务存档重新打开 | 已通过，同上；该脚本没有调用 Dify 或 LLM |
| 真实 game 与 PowerContext 进程重启后恢复 | 已通过，`restart.json` |
| Docker 源码构建与服务启动 | 已通过，9 个服务运行，`docker-deployment.json` |
| 真实 Dify 导入与发布 | 已通过，`dify-import.json` |
| 真实 Dify 禁网沙箱 Code 节点及后端/PowerContext 准备阶段 | 已通过，`dify-configuration-failure.json`；模型未配置时正确进入失败分支，不记作完整模型成功 |
| 真实 Qwen 调用 | 已通过，实际返回 `qwen3.7-plus`，`model-check.json` |
| 全容器 Dify→Qwen→PowerContext 流程 | 10 轮真实调用通过，三结局、隔离及新聊天回忆；`dify-e2e.json` 包含实际节点成功状态、两次模型请求的模型名与记忆保存确认 |
| 容器内真实 PowerContext 集成及空业务卷初始化 | 已通过，`docker-powercontext-integration.json`；game 与 PowerContext 使用新建数据卷 |
| game 与 PowerContext 容器重启后恢复 | 已通过，`docker-restart.json` |
| 第二套完全独立的全空栈安装 | 未另行重复；本次使用专用 Dify 部署，后接空业务卷完成全容器验收，具体范围见 `docker-deployment.json` |
| 浏览器页面视觉检查 | 自动化浏览器连接不可用；上述玩法通过真实已发布 Dify API 验证，未声称完成浏览器视觉验收 |
| 官方得分 | 尚无本作品的平台结果 |

本机及容器 PowerContext 均使用固定官方 commit `c4c95e465775b5b87ccac796194e01086bd7cc8f` 源码安装，构建标识 `1.5.0+git.c4c95e465775` 不声称是官方发布版本号。交付 Dockerfile 只拉取指定 commit，核对完整 SHA 后安装，避免下载整个仓库历史；使用显式源码构建标识避免浅克隆缺失版本 tag 造成歧义。

`dify-configuration-failure.json` 是配置模型之前的真实故障路径记录，不代表当前模型仍未配置。当前成功证据以 `dify-e2e.json` 为准。接口密钥、管理员密码和玩家存档访问码不进入证据。
