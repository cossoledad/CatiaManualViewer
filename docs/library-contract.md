# B 轮文档库与来源位置契约 v1

本契约定义核心、GUI、检索和未来 agent 共用的数据类型，代码在 `core/library_contract.py`，仅依赖标准库。A 轮已经提供类型、序列化及边界验证；B 轮再实现持久化、清单扫描、来源提取及重定位。当前 SQLite 根目录隔离与旧阅读历史仍保持原有格式，不宣称已支持数据库搬迁复用。

## 身份与挂载

| 类型 | 字段 | 规则 |
| --- | --- | --- |
| LibraryIdentity | library_id、label、release、language | library_id 为首次登记生成并持久化的 UUID；不由安装路径生成；不同版本或语言不默认合并 |
| LibraryMount | library_id、root | root 是本机绝对路径，只存于挂载配置；不进入可分享来源引用、模型缓存身份或 document_id |
| DocumentRef | library_id、path | path 为解码后的库内相对文件名，使用 POSIX 分隔符及 Unicode NFC；保留原始大小写和字面百分号，不再进行 URL 解码 |

`document_id = SHA256(canonical_library_uuid + LF + canonical_relative_path)`。同一库在不同磁盘挂载时 ID 不变；原文修改只改变 content_hash，不改变文章 ID。Windows 大小写冲突应在扫描时报告，不能静默合并。入口处的 URL 解析与实际文件路径仍须经过根目录校验。

库身份记录与内容清单放在应用数据目录，不能要求向只读手册写入文件。B 轮清单至少记录 schema_version、library_id、release、language 以及按相对路径排序的文件条目（path、字节数、SHA-256、资源类型）；manifest_hash 由规范化清单内容计算，排除根路径和 mtime。未完成扫描的清单不能标记为已验证。

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

搜索命中将包含 DocumentRef、SourceLocation、命中片段、通道与排序依据。图谱边包含关系类型、来源位置和生成依据，区分正文事实、显式引用、语义相关及模型建议。用户收藏与笔记引用来源契约，独立于可重建索引，不能随重建删除。

B 轮数据库迁移须保留现有索引作回退。导出使用 SQLite 一致快照或备份 API，不能在 WAL 活跃时只复制主文件。库 ID、分块版本、Embedding 模型与维度、术语版本和翻译上下文指纹均需显式记录；磁盘路径不参与模型内容缓存身份。
