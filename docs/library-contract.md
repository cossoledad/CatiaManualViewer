# B 轮文档库与来源位置契约 v1

本契约定义核心、GUI、检索和未来 agent 共用的数据类型，代码在 `core/library_contract.py`，仅依赖标准库。A 轮提供类型、序列化及边界验证；B 轮已经实现 SQLite schema 2 持久化、完整清单、副本验证、来源提取与重定位。界面、CLI 和核心搜索共用相同来源格式，详见 [B 轮实现](stage-b.md)。

## 身份与挂载

| 类型 | 字段 | 规则 |
| --- | --- | --- |
| LibraryIdentity | library_id、label、release、language | library_id 为首次登记生成并持久化的 UUID；不由安装路径生成；不同版本或语言不默认合并 |
| LibraryMount | library_id、root | root 是本机绝对路径，只存于挂载配置；不进入可分享来源引用、模型缓存身份或 document_id |
| DocumentRef | library_id、path | path 为解码后的库内相对文件名，使用 POSIX 分隔符及 Unicode NFC；保留原始大小写和字面百分号，不再进行 URL 解码 |

`document_id = SHA256(canonical_library_uuid + LF + canonical_relative_path)`。同一库在不同磁盘挂载时 ID 不变；原文修改只改变 content_hash，不改变文章 ID。Windows 大小写冲突应在扫描时报告，不能静默合并。入口处的 URL 解析与实际文件路径仍须经过根目录校验。

库身份记录与内容清单放在应用数据目录，不向只读手册写入文件。清单导出记录 schema_version、library_id、release、language 以及按相对路径排序的文件条目（path、size、content_hash、kind）；manifest_hash 由版本、release、language、files 的规范化 JSON 计算，排除库 UUID、根路径和 mtime。未完成、部分或失败扫描不能标记为已验证。

扫描包含文档、图片、示例及其他资源，包括手册自带的数据库文件。只有索引自身、WAL/SHM、同目录渲染缓存和备份目录作为应用维护文件排除；库内排除路径显式记录为 manifest_excludes，副本验证使用相同规则。目录链接、符号链接、Windows 大小写或 Unicode 规范化冲突会报告错误，不能静默漏扫。

迁移须选择已有库并验证副本，不能仅凭相同的目录名或少量抽样就直接绑定旧索引。完整清单一致可复用；同库修订须显式确认并增量更新；其他版本默认新库。清单、解析器、分块器与模型版本分别记录，避免更换模型导致重解析所有文档。

## 来源位置

SourceLocation 的 JSON 结构：

```json
{
  "schema_version": 1,
  "document": {
    "library_id": "41b2046c-9387-4719-9388-a2bd22fc1474",
    "path": "online/prtug_C2/prtugbt0501.htm"
  },
  "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "fragment": "",
  "block_id": "",
  "heading_path": ["Creating Pads", "Limits"],
  "text_quote": "Select the sketch",
  "text_prefix": "",
  "text_suffix": "and click Pad",
  "offset_css_px": -12.5
}
```

- content_hash 始终为原始源文件字节的 SHA-256；渲染器版本另外记录。
- fragment 不含前导 `#`，保留原文锚点语义。
- block_id 是特定原文修订下的定位标识，不能假定跨原文改动稳定。翻译 DOM 片段和检索章节片段可不同，但都映射到同一来源。
- heading_path 是标题层级；quote 与前后文用于原文修订后的候选重定位。
- offset_css_px 是定位块相对视口上缘的偏移，使用 CSS 像素，可为负数，不能是 NaN 或无穷值。
- 所有定位信息都是数据，不包含 JavaScript、shell 命令或安装绝对路径。未知 schema_version 必须明确拒绝，不能猜测转换。

B 轮重定位规则：相同 content_hash 优先定位 block_id 并应用偏移；显式点击链接优先其 fragment。修订变化后先尝试仍存在的锚点，再结合标题路径和 quote/context 找唯一候选；重复内容存在歧义时返回 ambiguous，无法定位返回 missing，不静默跳到第一个同名段落。

## 检索、图谱与用户数据

搜索命中已包含 document_id、SourceLocation、heading_path、snippet、score 和 reason；QueryPlan 提供查询展开、方法、提示及拼写建议。结果保留兼容的 path/title/module 字段。分块与渲染共享来源标记，标题、段落和表格文本不会因标记移动图片或链接。

reader_locations 持久化来源位置，term_aliases 保存用户别名；重建索引不删除它们或其他用户表。未来图谱边应包含关系类型、来源位置和生成依据，区分正文事实、显式引用、语义相关及模型建议。收藏、笔记与图谱尚未实现，须继续遵循此契约。

B 轮数据库升级前保留 schema 1 一致性备份。导出及副本挂载使用 SQLite backup API，包含已提交 WAL；目标必须是新文件。挂载只修改新副本的 root，原数据库保留原绑定。完整清单相同保留库 UUID；明确接受的同库修订保留 UUID，但将 manifest_verified 设为 false，等待完整更新。

当前分别记录 parser_version、chunk_version、catalog_hash 与 terminology_version；来源协议版本为 1，索引 schema 为 2。内置术语版本也在 stats 输出中提供，更新术语只影响查询展开。离线翻译 provider 身份使用模型内容修订，不再使用模型安装路径，旧缓存升级时保留并迁移。未来引入 Embedding 时再记录模型、维度与向量 schema；本轮没有向量索引或模型推理检索。

C 轮已实现独立向量扩展，见 [混合检索](stage-c.md)：semantic_schema_version=1，模型规格、384 维预设、float32-le、余弦距离、分段版本和语料签名显式记录。向量结果使用同一 SourceLocation，增加 channels、semantic_score、signals；目录搬迁不改变模型内容指纹。上段“不含向量索引”描述的是 B 轮交付范围。
