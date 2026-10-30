"""Functional catalog from the installed documentation's own TOC, independent of Qt."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from catia_manual.core.paths import within_root

CATEGORY_NAMES = {
    "Infrastructure": "基础与平台",
    "Mechanical Design": "机械设计",
    "Shape Design & Styling": "曲面与造型",
    "Analysis": "分析与仿真",
    "Equipment & Systems Engineering": "设备与系统工程",
    "Machining": "数控加工",
    "Product Synthesis": "产品综合与知识工程",
}
MANUAL_NAMES = {
    "Part Design": "零件设计",
    "Assembly Design": "装配设计",
    "Sketcher": "草图设计",
    "Generative Drafting": "创成式工程图",
    "Interactive Drafting": "交互式工程图",
    "Generative Shape Design & Optimizer": "创成式外形设计与优化",
    "FreeStyle Shaper Optimizer & Profiler": "自由曲面设计与优化",
    "FreeStyle Sketch Tracer": "自由曲面草图描摹",
    "Shape Sculptor": "造型雕塑",
    "Digitized Shape Editor": "数字化外形编辑",
    "Quick Surface Reconstruction": "快速曲面重建",
    "Generative Sheetmetal Design": "创成式钣金设计",
    "Sheet Metal Design": "钣金设计",
    "Wireframe and Surface": "线框与曲面",
    "Wireframe and Surface Design": "线框与曲面设计",
    "Generative Structural Analysis": "创成式结构分析",
    "Advanced Meshing Tools": "高级网格工具",
    "Knowledge Advisor": "知识顾问",
    "Knowledge Expert": "知识专家",
    "Product Engineering Optimizer": "产品工程优化",
    "Product Knowledge Template": "产品知识模板",
    "Product Structure": "产品结构",
    "Infrastructure": "基础操作",
    "Installation and Deployment": "安装与部署",
    "Automation Documentation Home Page": "自动化与 API",
    "DMU Navigator": "数字样机导航",
    "DMU Kinematics Simulator": "数字样机运动仿真",
    "DMU Space Analysis": "数字样机空间分析",
    "DMU Fitting Simulator": "数字样机装配仿真",
    "DMU Optimizer": "数字样机优化",
    "DMU Engineering Analysis Review": "数字样机分析评审",
    "Prismatic Machining": "棱柱加工",
    "Surface Machining": "曲面加工",
    "Lathe Machining": "车削加工",
    "Advanced Machining": "高级加工",
    "NC Manufacturing Infrastructure": "数控加工基础",
    "NC Manufacturing Review": "数控加工评审",
    "NC Manufacturing Verification": "数控加工验证",
    "Photo Studio": "摄影工作室",
    "Real Time Rendering": "实时渲染",
    "Component Catalog Editor": "组件目录编辑器",
    "Electrical Harness Installation": "电气线束安装",
    "Electrical Wire Routing": "电气布线",
    "Electrical Harness Flattening": "电气线束展平",
    "Electrical Library": "电气元件库",
    "Circuit Board Design": "电路板设计",
    "Tubing Design": "管路设计",
    "Piping Design": "配管设计",
    "Mold Tooling Design": "模具设计",
    "Weld Design": "焊接设计",
    "Structure Design": "结构设计",
    "Composites Design": "复合材料设计",
    "Functional Tolerancing & Annotation": "功能公差与标注",
}


def category_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@dataclass(frozen=True)
class Manual:
    id: str
    title: str
    label: str
    entry: str
    toc: str | None
    search_modules: tuple[str, ...]


@dataclass
class Category:
    id: str
    title: str
    label: str
    manuals: list[Manual] = field(default_factory=list)

    @property
    def modules(self) -> list[str]:
        return list(
            dict.fromkeys(module for manual in self.manuals for module in manual.search_modules)
        )


class ManualCatalog:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.categories: list[Category] = []
        self.manuals: dict[str, Manual] = {}
        self.load()

    def make_manual(self, module: str, title: str) -> Manual | None:
        if not module or Path(module).name != module or module in {".", ".."}:
            return None
        folder = within_root(self.root, self.root / "online" / module)
        if not folder.is_dir():
            return None
        maps = sorted(p for p in folder.glob("*sm.htm") if not p.name.endswith("ssm.htm"))
        overviews = sorted(folder.glob("*pr01.htm"))
        home = sorted(folder.glob("*AutomationHome.htm"))
        choices = overviews or home or maps or sorted(folder.glob("*.htm"))
        if not choices:
            return None
        modules = [module]
        if module == "CAAScdBase":
            modules += [
                p.name
                for p in (self.root / "online").iterdir()
                if p.is_dir()
                and (p.name == "interfaces" or p.name.startswith(("CAAScd", "CAAScr")))
            ]
        return Manual(
            module,
            title,
            MANUAL_NAMES.get(title, title),
            choices[0].relative_to(self.root).as_posix(),
            maps[0].relative_to(self.root).as_posix() if maps else None,
            tuple(dict.fromkeys(modules)),
        )

    def load(self):
        online = self.root / "online"
        if not online.is_dir():
            return
        indexes = sorted(online.glob("CATIA*_TOC.xml"), key=lambda p: ("P3" not in p.name, p.name))
        if indexes:
            try:
                tree = ET.fromstring(indexes[0].read_bytes())
                product = tree.find("ITEM")
                for node in product.findall("ITEM") if product is not None else []:
                    title = node.get("name", "Reference")
                    category = Category(category_id(title), title, CATEGORY_NAMES.get(title, title))
                    for item in node.findall(".//DITEM"):
                        manual = self.make_manual(
                            item.get("dir", ""), item.get("name", "Reference")
                        )
                        if manual and manual.id not in {m.id for m in category.manuals}:
                            category.manuals.append(manual)
                            self.manuals[manual.id] = manual
                    if category.manuals:
                        self.categories.append(category)
            except (ET.ParseError, OSError, ValueError):
                self.categories.clear()
                self.manuals.clear()
        if self.categories:
            return
        # Older/cut-down copies may omit TOC.xml. Recover the same categories from the homepage.
        for homepage in sorted(online.glob("CATIA*_default.htm"))[:1]:
            soup = BeautifulSoup(homepage.read_bytes(), "lxml")
            seen = set()
            for anchor in soup.find_all("a", onmouseover=True):
                title = anchor.get_text(" ", strip=True)
                match = re.search(r"visualize_div\((\d+),(.+?)\);", anchor["onmouseover"], re.S)
                if not title or not match or match[1] in seen:
                    continue
                seen.add(match[1])
                group = Category(category_id(title), title, CATEGORY_NAMES.get(title, title))
                for module in re.findall(r"'([^']+)'", match[2]):
                    toc = sorted((online / module).glob("*CATIAtoc.htm"))
                    label = module
                    if toc:
                        page = BeautifulSoup(toc[0].read_bytes(), "lxml")
                        label = page.title.get_text(" ", strip=True) if page.title else module
                    manual = self.make_manual(module, label)
                    if manual:
                        group.manuals.append(manual)
                        self.manuals[manual.id] = manual
                if group.manuals:
                    self.categories.append(group)
        if not self.categories:
            group = Category("reference", "Reference", "参考文档")
            for folder in sorted(online.iterdir()):
                maps = sorted(folder.glob("*sm.htm")) if folder.is_dir() else []
                if maps:
                    page = BeautifulSoup(maps[0].read_bytes(), "lxml")
                    title = page.h1 or page.title
                    manual = self.make_manual(
                        folder.name, title.get_text(" ", strip=True) if title else folder.name
                    )
                    if manual:
                        group.manuals.append(manual)
                        self.manuals[manual.id] = manual
            if group.manuals:
                self.categories.append(group)

    def category(self, identity: str) -> Category:
        for group in self.categories:
            if identity in {group.id, group.title, group.label}:
                return group
        raise ValueError(f"未知功能分类：{identity}")

    def scope(self, manuals):
        from catia_manual.core.scopes import FunctionalScope

        return FunctionalScope(
            [module for identity in manuals for module in self.manuals[identity].search_modules],
            manuals,
        )
