import re
from dataclasses import dataclass, field

CJK = re.compile(r"[\u3400-\u9fff]")
# Alternatives within one concept use OR; separate concepts use AND.
TERMS = {
    "零件设计": ("part design",),
    "装配设计": ("assembly design",),
    "创成式外形设计": ("generative shape design",),
    "线框和曲面": ("wireframe surface",),
    "草图设计器": ("sketcher",),
    "超级副本": ("powercopy",),
    "多截面实体": ("multi section solid", "loft"),
    "多截面曲面": ("multi section surface",),
    "镜像延伸": ("mirrored extent",),
    "装配约束": ("assembly constraint",),
    "边线圆角": ("edge fillet",),
    "可变半径": ("variable radius",),
    "几何图形集": ("geometrical set",),
    "几何集合": ("geometrical set",),
    "工程图": ("drawing", "drafting"),
    "工作台": ("workbench",),
    "凸台": ("pad",),
    "拉伸": ("pad", "extrude", "extrusion"),
    "凹槽": ("pocket",),
    "挖切": ("pocket",),
    "草图": ("sketch", "sketcher"),
    "圆角": ("fillet",),
    "倒角": ("chamfer",),
    "旋转体": ("shaft",),
    "旋转槽": ("groove",),
    "扫掠": ("sweep", "rib"),
    "开槽": ("slot",),
    "孔": ("hole",),
    "抽壳": ("shell",),
    "拔模": ("draft",),
    "约束": ("constraint",),
    "轮廓": ("profile",),
    "曲面": ("surface",),
    "曲线": ("curve",),
    "平面": ("plane",),
    "直线": ("line",),
    "几何体": ("body",),
    "零件": ("part",),
    "装配": ("assembly",),
    "参数": ("parameter",),
    "尺寸": ("dimension",),
    "半径": ("radius",),
    "直径": ("diameter",),
    "厚度": ("thickness",),
    "长度": ("length",),
    "方向": ("direction",),
    "偏置": ("offset",),
    "镜像": ("mirror",),
    "阵列": ("pattern",),
    "布尔": ("boolean",),
    "修剪": ("trim",),
    "分割": ("split",),
    "缝合": ("sew", "join"),
    "投影": ("projection", "project"),
    "相交": ("intersection",),
    "测量": ("measure",),
    "材料": ("material",),
    "公式": ("formula",),
    "宏": ("macro",),
    "接口": ("interface",),
    "创建": ("create",),
    "修改": ("modify", "edit"),
    "编辑": ("edit",),
    "删除": ("delete", "remove"),
    "打开": ("open",),
    "保存": ("save",),
    "导入": ("import",),
    "导出": ("export",),
    "选择": ("select",),
}
FILLERS = (
    "如何",
    "怎么",
    "怎样",
    "为什么",
    "进行",
    "使用",
    "操作",
    "功能",
    "方法",
    "教程",
    "文档",
    "帮助",
    "中的",
    "一个",
    "的",
    "在",
    "与",
    "和",
    "及",
    "来",
)
ENGLISH_STOPWORDS = {"how", "to", "the", "a", "an", "do", "i", "can", "is", "of", "in", "use"}


def words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9]+", text)


def phrase(text: str) -> str:
    return " AND ".join('"' + token + '"*' for token in words(text))


@dataclass
class QueryPlan:
    original: str
    match: str
    expanded: str
    unknown: str = ""
    method: str = "english"
    notes: list[str] = field(default_factory=list)


def plan_query(query: str) -> QueryPlan:
    query = query.strip()[:500]
    if not CJK.search(query):
        return QueryPlan(query, phrase(query), query)
    remaining = query
    groups, expanded = [], []
    for chinese in sorted(TERMS, key=len, reverse=True):
        if chinese not in remaining:
            continue
        alternatives = TERMS[chinese]
        groups.append("(" + " OR ".join("(" + phrase(term) + ")" for term in alternatives) + ")")
        expanded.append(" / ".join(alternatives))
        remaining = remaining.replace(chinese, " ")
    for filler in FILLERS:
        remaining = remaining.replace(filler, " ")
    latin = words(remaining)
    if latin:
        groups.append(phrase(" ".join(latin)))
        expanded.extend(latin)
    unknown = " ".join(re.findall(r"[\u3400-\u9fff]+", remaining))
    return QueryPlan(
        query,
        " AND ".join(groups),
        " + ".join(expanded),
        unknown,
        "terminology",
        ["中文术语映射到英文原文"],
    )
