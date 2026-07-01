# 03 — 写一个自定义 Extractor

假设你想为 spec 中的"电源域 (Power Domain)"信息抽取节点。

## 1. 创建包

```bash
mkdir docgraph-power-domain && cd docgraph-power-domain
# pyproject.toml + my_pkg/extractors.py
```

## 2. 写 Extractor

```python
# my_pkg/extractors.py
from docgraph.extractors.base import Extractor, ExtractContext, ExtractResult
from docgraph.graph.schema import (
    Node, NodeKind, ParsedDoc, Edge, EdgeKind, Evidence, Location,
)
from docgraph.core.ids import content_hash, make_node_id
import re


class PowerDomainExtractor:
    """识别电源域（VDDIO / VBAT / VDDA / VSS 等）"""
    name = "power_domain"
    kinds = {NodeKind.MODULE}
    requires = {"section"}  # 等 SectionExtractor 跑完
    version = "0.1"

    _RE = re.compile(r"\b(V(DD|SS|BAT|REF|DDA)[A-Z0-9_]{0,8})\b")

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult:
        seen: set[str] = set()
        nodes: list[Node] = []
        for page in doc.pages:
            text = page.text
            for m in self._RE.finditer(text):
                name = m.group(1)
                if name in seen:
                    continue
                seen.add(name)
                nodes.append(Node(
                    id=make_node_id(ctx.family, NodeKind.MODULE, f"power.{name}"),
                    kind=NodeKind.MODULE,
                    name=name,
                    qualified_name=f"power.{name}",
                    doc_id=doc.doc_id,
                    location=Location(page=page.page_no),
                    attrs={"category": "power_domain"},
                    summary=f"Power domain: {name}",
                    hash=content_hash(name),
                ))
        return ExtractResult(nodes=nodes)
```

## 3. 注册 entry_point

```toml
# pyproject.toml
[project]
name = "docgraph-power-domain"
version = "0.1.0"
dependencies = ["docgraph"]

[project.entry-points."docgraph.extractors"]
power_domain = "my_pkg.extractors:PowerDomainExtractor"
```

## 4. 用户安装并启用

```bash
pip install docgraph-power-domain
```

```yaml
# .docgraph/config.yaml
extractors:
  enabled:
    - section
    - register
    - power_domain   # ← 加在这里
```

```bash
docgraph plugins ls          # 应该能看到 power_domain
docgraph build --force
docgraph search VDD --kind=module
```

## 5. 自定义边类型

`EdgeKind` 当前是固定 enum。M4 会支持 plugin 注册自定义边类型；M3 阶段可以临时把它们存为 `attrs` 上的字段，或者复用 `DEPENDS_ON / CONTROLS` 等通用边。

## 6. 测试

```python
# tests/test_my_extractor.py
from my_pkg.extractors import PowerDomainExtractor
from docgraph.extractors.base import ExtractContext
from docgraph.graph.schema import ParsedDoc, ParsedPage, TextBlock

def test_basic():
    parsed = ParsedDoc(doc_id="d", source_path="x", pages=[
        ParsedPage(page_no=1, text_blocks=[
            TextBlock(text="The chip is powered by VDDIO and VBAT.")
        ])
    ])
    res = PowerDomainExtractor().extract(parsed, ExtractContext(family="t"))
    names = {n.name for n in res.nodes}
    assert {"VDDIO", "VBAT"}.issubset(names)
```
