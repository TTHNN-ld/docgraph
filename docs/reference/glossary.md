# 术语表

| 缩写 | 含义 |
|---|---|
| Spec | Specification，泛指芯片相关的规范文档 |
| Datasheet | 数据手册 |
| TRM | Technical Reference Manual，技术参考手册 |
| Errata | 勘误表 |
| IP-XACT | IEEE 1685，IP 描述标准 |
| SystemRDL | SystemRDL，寄存器描述语言 |
| VLM | Vision-Language Model |
| MCP | Model Context Protocol |
| IR | Intermediate Representation，中间表示 |
| xref | Cross-reference，交叉引用 |
| ADR | Architecture Decision Record，架构决策记录 |
| RFC | Request for Comments，征求意见稿 |
| BFS / DFS | 广度优先 / 深度优先搜索 |
| CTE | Common Table Expression，公共表表达式（SQL 递归查询） |
| RAG | Retrieval-Augmented Generation，检索增强生成 |

## 项目术语

| 术语 | 含义 |
|---|---|
| Node | 图谱中的实体节点 |
| Edge | 图谱中的关系边 |
| Evidence | 节点/边的来源证据（pages、bboxes、chunk IDs、extractor） |
| Confidence | 抽取/链接的置信度（0.0–1.0） |
| Manifest | `.docgraph/manifest.json`，文件状态追踪 |
| Family | 项目命名空间；也是 EntityResolver 无法推断芯片实例时的回退键 |
| FederationLinker | 在同一项目内按芯片实例和来源优先级连接多份文档的 Linker |
| ParsedDoc | Parser 输出的统一中间表示 |
| Tier | LLM 模型分层：`fast` / `balanced` / `accurate` |
| Walking Skeleton | 用最小端到端路径尽早验证核心架构的开发方式 |

## 芯片 spec 高频术语

| 术语 | 含义 |
|---|---|
| Register | 寄存器 |
| Bitfield | 位域 |
| Pin | 物理管脚 |
| Signal | 内部信号 |
| Module / IP | 功能模块 / IP 核 |
| Interface | 总线 / 协议接口 |
| Timing parameter | 时序参数 |
| Electrical parameter | 电气参数 |
| Reset value | 复位值 |
| Access type | 读写权限（RO/RW/WO/W1C 等） |
