# 导出 IP-XACT / SystemRDL

导出器读取 register/bitfield 节点，生成基础寄存器描述。

```bash
mkdir -p out
docgraph export systemrdl out/registers.rdl
docgraph export systemrdl out/csw.rdl --register CSW --component my_ip

docgraph export ipxact out/component.xml
docgraph export ipxact out/csw.xml --register CSW --component my_ip
```

当前只承诺 IP-XACT IEEE 1685-2014 和 SystemRDL 2.0 的 register/field 基础子集，不覆盖完整 memory map、vendor extension 或全部语法。

导出前运行：

```bash
docgraph doctor --strict
docgraph l2 audit --schema register
docgraph inspect register CSW
```

下游生成 RTL、UVM RAL 或 C header 前，仍需校验 base address、offset、register width、field range、access、reset、保留位和跨文档冲突。兼容性必须通过目标 EDA 工具的真实解析测试确认。
