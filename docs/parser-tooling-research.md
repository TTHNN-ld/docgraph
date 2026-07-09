# 文档解析与视觉检索工具调研

## 1. 调研背景

DocGraph 的目标不是简单把 PDF 转成 Markdown，而是为芯片规格书、技术手册、接口文档建立可追溯的知识底座。这个目标对解析层有几个直接要求：

- L0 必须尽量保留原始版面信息，包括页码、坐标、阅读顺序、表格单元格、图片、公式、caption 和原始证据。
- L1 需要把 L0 组织成可检索 chunk，并且每个 chunk 能稳定回溯到 L0 block。
- L2 可以抽取寄存器、信号、接口、时序、memory map、约束等实体，但 L2 的任何结论都不能脱离 L0/L1 证据。
- 芯片文档里大量信息存在于表格、框图、时序图、pin diagram、跨页表和图文混排中，单纯文本抽取很容易丢信息。

因此，本次调研重点关注两类能力：

1. **文档解析工具**：负责把 PDF、Office、图片等文档转换成结构化内容。
2. **视觉检索 / Pixel-level RAG 工具或方法**：负责在解析不稳定、图表信息较多时，直接基于页面图像或局部区域做检索和问答。

## 2. 总体判断

目前主流工具大致分成三档。

第一档是 **结构化解析器**，代表是 Docling、MinerU、Marker、Unstructured、LlamaParse。这类工具会识别 layout、表格、图片、OCR、阅读顺序，适合成为 DocGraph 的 L0 parser backend。

第二档是 **轻量格式转换器**，代表是 MarkItDown。这类工具更适合把各种文件快速转成 Markdown 给 LLM 使用，但通常不强调 lossless layout，不适合作为 DocGraph 主解析器。

第三档是 **视觉检索方法**，代表方向包括 ColPali、Visual Document Retrieval、Pixel-grounded RAG。这类方法不是传统 parser，而是直接把页面图像、图片区域或表格截图纳入检索。它适合补充 DocGraph 的召回能力，尤其适合芯片文档里的框图、波形、复杂表格和版面依赖信息。

## 3. 工具调研

### 3.1 Docling

Docling 是 IBM/Docling 项目下的文档解析框架，支持 PDF、DOCX、PPTX、XLSX、HTML、图片等输入。它的核心优势是有统一的 `DoclingDocument` 表示，并支持导出 Markdown、HTML、JSON 等格式。官方文档和 README 中明确强调了 PDF layout、阅读顺序、表格结构、OCR、多格式输入、本地运行和 agent 集成能力。

从 DocGraph 的角度看，Docling 比普通 Markdown 转换器更接近理想的 L0 parser。原因是它不是只产出一段文本，而是能保留文档结构，后续可以映射到 DocGraph 的 block、table、figure、page、bbox、chunk 等模型。

适合场景：

- 多格式文档接入，不只 PDF。
- 需要统一输出模型，降低不同 parser 接入成本。
- 需要在本地运行，避免规格书上传云端。
- 需要较稳定的 layout、table、OCR 和 reading order。

风险点：

- 需要实际验证复杂芯片 PDF 的表格、公式、框图效果。
- 输出模型需要和 DocGraph L0 做一层严谨映射，不能只接 Markdown。
- 对超大 PDF 的速度、内存和失败恢复策略要单独压测。

结论：**Docling 值得作为 DocGraph 的重点候选 parser backend 接入**。优先级应高于 MarkItDown。

### 3.2 MinerU

MinerU 主要面向 PDF 文档解析，尤其重视版面分析、公式、表格、图片和 OCR。它适合论文、技术文档、手册一类结构复杂的 PDF。DocGraph 当前已经接入 MinerU，并把它作为复杂 PDF 的主要解析能力之一。

适合场景：

- 芯片规格书中的复杂表格、图、公式、扫描页。
- PyMuPDF 只能拿到碎片文本、表格结构丢失或章节树异常的 PDF。
- 对图片、caption、公式和表格有较高保真需求的文档。

风险点：

- 模型和运行依赖重，初始化和构建时间比 PyMuPDF 高。
- 版本兼容性要控制，magic-pdf/MinerU API 变化可能影响集成。
- 不能把 MinerU 输出直接当最终语义结论，仍然要经过 DocGraph L0 归一化和质量检查。

结论：**MinerU 适合保留为复杂 PDF 的高精度解析器**。默认策略不宜所有文档都强制 MinerU，否则构建时间会影响体验。

### 3.3 Marker

Marker 是一个文档转换项目，支持把 PDF、图片、Office、HTML、EPUB 等转换成 Markdown、JSON、HTML 或 chunk。它对表格、公式、图片提取有明确支持，也提供 LLM hybrid 模式，用于改善复杂表格、跨页内容和格式修复。

适合场景：

- 需要较快把技术 PDF 转成结构化 Markdown/JSON。
- 需要公式、表格、图片抽取。
- 可以接受在部分复杂场景使用 LLM 修复。

风险点：

- 许可证和商用约束需要确认。对芯片公司内部生产使用，这一点不能含糊。
- LLM hybrid 模式会引入成本、延迟和稳定性问题。
- 如果只用 Markdown 输出，仍然不足以满足 DocGraph L0 的无损要求。

结论：**Marker 技术上值得评测，但不应在许可证和输出结构验证前作为默认 backend**。

### 3.4 MarkItDown

MarkItDown 是 Microsoft 开源的轻量转换工具，目标是把 PDF、Office、图片、音频、HTML、CSV、JSON、XML、ZIP、EPUB 等格式转成 Markdown，方便 LLM 和文本分析工具消费。

它的定位很清楚：轻量、好用、覆盖格式广。它不是一个强调版面无损、表格单元格完整、图片区域定位和阅读顺序质量门的解析框架。

适合场景：

- 快速导入非核心文档。
- README、网页、Office、简单 PDF 转 Markdown。
- 作为 DocGraph 的轻量 fallback 或附加导入器。

不适合场景：

- 作为芯片规格书主解析器。
- 对寄存器表、pin 表、复杂表格、框图和时序图做高可信抽取。
- 需要 L0 坐标、block、table cell、figure crop、source evidence 的生产流程。

结论：**MarkItDown 可以接，但它应该是轻量导入工具，不是 DocGraph 的主解析层**。

### 3.5 Unstructured

Unstructured 提供文档 partition、clean、chunk、extract 等能力，常用于 RAG 和 ETL 前处理。它支持多种文档格式，也有 element 级解析思想，对构建 RAG pipeline 有参考价值。

适合场景：

- 通用企业文档 ingestion。
- 快速搭建 RAG 原型。
- 借鉴 partition/chunking 和 element schema 思路。

风险点：

- 开源版和生产服务能力边界需要区分。
- 对芯片规格书这种高精度场景，仍需验证复杂表格、图、坐标和证据链。
- 如果输出被简化成文本 element，仍然无法满足 DocGraph L0 无损要求。

结论：**Unstructured 可作为通用文档接入参考，但不是当前最优先的芯片规格书 parser**。

### 3.6 LlamaParse

LlamaParse 是 LlamaIndex 生态里的文档解析服务，面向 RAG 场景，强调 layout-aware、复杂 PDF、表格、图片、多模态解析能力。

适合场景：

- 不介意云服务依赖。
- 快速验证复杂 PDF 到 RAG 的效果。
- 对文档保密要求较低，或者已有私有化方案。

风险点：

- 芯片规格书通常有保密要求，云端解析可能不可接受。
- 成本、速率限制、稳定性和可审计性都要纳入生产评估。
- DocGraph 的核心路径更适合本地可控 parser。

结论：**LlamaParse 适合对标效果，不适合作为默认生产依赖**，除非后续有明确的私有化或合规方案。

## 4. PixelRAG 是什么

PixelRAG 不是一个传统意义上的 PDF 解析器。更准确地说，它代表一类 **视觉证据优先的 RAG 方法**：不完全依赖 OCR 或 Markdown，而是直接把页面图像、局部截图、表格区域、框图区域编码进检索系统。查询时先召回相关页面或区域，再交给 VLM 结合图像回答。

这类方法的价值在于，它能保留很多纯文本解析会丢掉的信息：

- 箭头方向、模块连接、层级框图。
- 时序图里的边沿、周期、setup/hold 关系。
- pin diagram 中的空间位置和复用标注。
- memory map/register table 中跨列、跨页、合并单元格关系。
- 文字、图、caption 之间的空间关系。

与 PixelRAG 类似的公开方向包括 ColPali、visual document retrieval、VDocRAG 等。ColPali 的思路是直接对页面图像做多向量表示，用视觉语言模型能力做文档检索，从而减少传统 OCR/text pipeline 的信息损失。

对 DocGraph 来说，PixelRAG 不应该替代 L0/L1/L2，而应该成为第四类证据通道：

```text
L0 原始版面证据
L1 文本与结构 chunk
L2 实体与关系图谱
Visual RAG 页面/区域视觉检索
```

尤其在 L2 尚不稳定时，视觉检索可以作为很好的兜底。agent 查询一个接口、寄存器、模块连接或时序约束时，不只看 L2 节点，还能回到相关页面截图、表格截图和框图截图做二次确认。

## 5. 对 DocGraph 的落地建议

### 5.1 Parser backend 分层

建议把 parser 接入分成三类：

| 类型 | 工具 | 用途 |
|---|---|---|
| 快速解析 | PyMuPDF | 默认快速路径，适合文本型 PDF、目录质量好、表格简单的文档 |
| 高精度解析 | MinerU、Docling、Marker | 复杂 PDF、表格/图片/公式较多、PyMuPDF 质量门不通过时使用 |
| 轻量导入 | MarkItDown、Unstructured | 非核心文档、Markdown 化导入、简单 Office/网页/说明文档 |

关键点是：不管底层 parser 是谁，最终必须归一到 DocGraph 的 L0 模型，而不是让上层直接消费各工具自己的 Markdown。

### 5.2 Parser 选择策略

生产环境不建议让用户记很多命令，也不建议所有 PDF 默认走最重的 parser。更合理的策略是自动判断：

1. 先用快速探针分析 PDF：
   - 是否有可用 PDF outline。
   - 文本层是否完整。
   - 表格密度和表格结构是否稳定。
   - 图片/扫描页比例。
   - heading 质量和章节连续性。
   - 页面复杂度、双栏、旋转、跨页表迹象。

2. 如果快速路径质量达标，用 PyMuPDF。

3. 如果触发复杂文档信号，切到 MinerU 或 Docling。

4. 如果仍然不达标，保留失败原因和审计报告，而不是悄悄产出低质量 L0/L1。

### 5.3 Docling 接入优先级

Docling 最适合作为下一阶段重点评测对象。建议做一个小规模 benchmark：

- case 中两个 PCIe spec。
- spec 目录中 3 到 5 个小型 ARM/技术手册。
- 至少覆盖文本型 PDF、表格密集 PDF、图较多 PDF、目录异常 PDF。

评测指标：

- L0 block 数量、表格 cell 完整率、figure/caption 保留率。
- L1 chunk 可回溯率、section tree 准确率。
- 解析耗时和内存。
- L2 register/signal/interface/module/timing 的召回和误抽。
- agent 回答任务时的正确率、证据引用质量、token 消耗和耗时。

### 5.4 Visual RAG 接入方式

Visual RAG 不建议一开始做得很重。可以先做一个最小闭环：

1. L0 保存 page image、figure crop、table crop。
2. 为这些视觉对象建立 embedding 索引。
3. 查询时同时召回 L1 chunk、L2 node 和视觉对象。
4. agent 回答前优先展示或读取相关截图证据。

后续如果效果好，再考虑引入 ColPali 一类页面级视觉检索模型。

## 6. 推荐路线

短期：

- 保持 PyMuPDF + MinerU 双路径。
- 加强 parser quality gate，避免章节树、表格和 figure 静默劣化。
- 将 Docling 加入评测，不急于默认启用。
- MarkItDown 只作为轻量导入器，不进入核心 PDF 主链路。

中期：

- 把 parser backend 抽象稳定下来，支持 PyMuPDF、MinerU、Docling、Marker 的统一 L0 输出。
- 引入视觉证据索引，对图、表、页面截图做召回。
- 对 L2 抽取结果增加来源类型、置信度、召回审计和人工 review 接口。

长期：

- 形成三路召回：L1 文本检索、L2 图谱检索、Visual RAG 视觉检索。
- 对芯片领域核心对象建立严格评测集：寄存器、bitfield、pin、signal、interface、module、clock/reset、interrupt、memory map、timing、constraint。
- 对每次 parser 或 extractor 升级做回归，防止某个工具看似更智能但悄悄损坏结构。

## 7. 参考资料

- Docling GitHub: https://github.com/docling-project/docling
- Docling documentation: https://docling-project.github.io/docling/
- MinerU GitHub: https://github.com/opendatalab/MinerU
- Marker GitHub: https://github.com/datalab-to/marker
- Microsoft MarkItDown GitHub: https://github.com/microsoft/markitdown
- Unstructured open source documentation: https://docs.unstructured.io/open-source
- LlamaParse documentation: https://docs.cloud.llamaindex.ai/llamaparse
- ColPali paper: https://arxiv.org/abs/2407.01449
- VDocRAG paper: https://arxiv.org/abs/2504.09795
