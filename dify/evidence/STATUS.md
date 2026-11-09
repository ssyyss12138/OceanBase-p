# 验证状态

本文件用于明确区分已经实测与尚未实测的能力，后续完成联调后更新。

| 项目 | 状态与证据 |
| --- | --- |
| Python 规则、三结局、重复结算、版本冲突与恢复日志 | 13 项测试通过，`tests/` |
| 真实 PowerContext Source / Memory 写入与 Context 读取 | 已通过，`powercontext-integration.json` |
| 玩家隔离、跨会话式读取与业务存档重新打开 | 已通过，同上；该脚本没有调用 Dify 或 LLM |
| 真实 game 与 PowerContext 进程重启后恢复 | 已通过，`restart.json` |
| Compose 配置语法检查 | 已通过；不代表镜像成功启动 |
| 真实 Dify 导入与发布 | 已通过，`dify-import.json` |
| 真实 Dify 禁网沙箱 Code 节点及后端/PowerContext 准备阶段 | 已通过，`dify-configuration-failure.json`；模型未配置时正确进入失败分支，不记作完整模型成功 |
| 真实 Qwen 调用及完整 Dify→Qwen→PowerContext 流程 | 等待本机配置实际模型地址与 API Key，尚未验收 |
| 从干净 Docker 卷完整复现 | 尚未验收 |
| 官方得分 | 尚无本作品的平台结果 |

本机 PowerContext 使用固定官方 commit `c4c95e465775b5b87ccac796194e01086bd7cc8f` 源码安装。GitHub 源码归档无 Git 版本元数据，本机构建使用标识 `1.5.0+git.c4c95e465775`；该值是源码构建标识，不声称它是官方发布版本号。交付 Dockerfile 从固定 Git commit 安装。
