# Roadmap

> 更新于 2026-08-27。本文件只记录当前状态和后续优先级；历史需求见[需求记录](./requirements-history.md)，重大设计取舍见 [RFC](../decisions/README.md)。

## 当前基线

已具备：

- PDF、DOCX、XLSX/XLSM、Markdown 的默认发现和基础解析。
- L0 blocks、L1 chunks、FTS、可插拔向量索引和 L0 回溯链。
- schema registry 与表格优先的 L2 抽取；L2 provenance、candidate/fact 状态和结构校验。
- 文件级增量构建、删除对账、parser fallback、manifest 审计。
- 基于实现与语义配置的构建指纹、项目级写锁、可回滚 Linker、向量完整性恢复，以及 success/degraded/failed 完成状态。
- CLI、6 工具 MCP stdio、Web UI、跨项目只读 federation、IP-XACT/SystemRDL 基础导出。
- `doctor --strict`、`l2 audit` 和 `l2 eval` 质量入口。

已知边界：

- DOCX 轻量 parser 不还原分页、浮动布局和嵌入图片；XLSX 不还原样式和版面。
- PDF 质量依赖实际后端；只有 PyMuPDF 时，扫描件和复杂表格可能降级。
- 增量粒度目前是文件，不是页面或单个 extractor stage。
- L2 的跨文档覆盖率尚未通过大规模、公开可复现的 golden set 证明。
- Web UI 无内置认证；导出只覆盖 register/field 等基础子集。

## 下一步优先级

1. 建立可公开复现的 L2 golden 数据集，并按实体类型维护 precision、recall、F1 与失败样例。
2. 提升复杂 PDF、DOCX 图片和表格的 L0 证据完整性，同时保持轻量默认安装。
3. 校准跨文档实体合并、candidate/fact 晋升和人工 review 的真实工作流。
4. 为导出和 federation 增加端到端兼容性测试，明确稳定支持的子集。

## 暂不优先

- 通用文档协作、权限与在线编辑。
- 为单一文档类型增加专用硬编码流程。
- 在缺少真实评测前引入更复杂的模型编排或大量配置项。
- 将 L2 变成读取文档的唯一入口。

## 已接受 RFC

- [RFC 0015：语义知识图谱与混合抽取](../decisions/0015-semantic-kg-hybrid-extraction.md)
- [RFC 0016：按上下文预算提供 L1 文档视图](../decisions/0016-adaptive-l1-context.md)
- [RFC 0017：L2 候选与事实可信状态](../decisions/0017-l2-candidate-fact-trust-model.md)
- [RFC 0018：面向 Agent 的 MCP v2 接口](../decisions/0018-mcp-v2-agent-interface.md)
- [RFC 0019：显式语义检索与可解释候选融合](../decisions/0019-explicit-semantic-retrieval.md)
- [RFC 0020：分阶段失效与可恢复索引构建](../decisions/0020-stage-aware-index-build.md)
