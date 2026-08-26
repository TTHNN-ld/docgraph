# Parser 后端选型快照

> 记录日期：2026-08-26。本页只保留选型依据，不定义当前安装、路由或支持矩阵；实际行为见[文档导入](../architecture/ingestion.md)。采用任何第三方工具前都应重新核对版本、许可证和上游文档。

## 评估标准

DocGraph 需要的是可映射到 L0 的结构化证据，不只是 Markdown 文本。后端评估重点是：页码与坐标、阅读顺序、表格 cells、图和公式、OCR、失败可见性、离线能力、资源成本和许可证。

## 当前结论

| 工具 | 定位 | 在 DocGraph 中的结论 |
|---|---|---|
| PyMuPDF | 轻量 PDF 解析 | 核心快速路径和最终兜底 |
| Docling | 结构化、多格式解析 | 复杂 PDF 的可选质量后端 |
| MinerU | OCR、公式、表格、版面 | 复杂/扫描 PDF 的可选高保真后端，支持远程推理 |
| Marker | PDF 到结构化输出 | 可选后端；与 MinerU 依赖互斥，使用前确认许可证 |
| MarkItDown | 多格式转 Markdown | 适合轻量导入，不满足高保真 L0 主链要求 |
| Unstructured | 通用文档 ETL/RAG | 可借鉴 element 模型，不是当前优先后端 |
| LlamaParse | 云端 layout-aware 服务 | 可作效果对标；保密、成本与可审计性限制默认使用 |

无论后端输出 Markdown、JSON 还是内部对象，都必须先归一到 `ParsedDoc/Block`；上层不得直接依赖后端格式。

## Visual RAG

页面或区域级视觉检索可以补充框图、时序图、pin diagram 和复杂表格召回，但不替代三层模型：

```text
L0 版面证据 + L1 文本检索 + L2 实体关系 + 可选视觉索引
```

只有在已有 page/figure/table crop、可回溯 ID 和真实评测集后，才值得引入视觉索引；不能用更重的模型掩盖 L0 证据缺失。

## 后端评测要求

在改变默认路由前，使用同一组代表性文档比较：

- 表格 cell、figure/caption、公式、章节和坐标保留率。
- L1 来源可回溯率与 section tree 质量。
- 解析时间、峰值内存、模型下载和失败恢复。
- 下游 L2 precision/recall，而非只比较 Markdown 外观。
- Agent 答案正确性、证据质量、token 和端到端时间。

## 上游资料

- [Docling](https://github.com/docling-project/docling)
- [MinerU](https://github.com/opendatalab/MinerU)
- [Marker](https://github.com/datalab-to/marker)
- [MarkItDown](https://github.com/microsoft/markitdown)
- [Unstructured](https://docs.unstructured.io/open-source)
- [LlamaParse](https://docs.cloud.llamaindex.ai/llamaparse)
- [ColPali paper](https://arxiv.org/abs/2407.01449)
