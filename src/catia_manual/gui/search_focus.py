"""Search emphasis without changing source text, anchors or translation units."""

import html
import json
import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAbstractTextDocumentLayout, QPalette, QTextDocument
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from catia_manual.core.query import ENGLISH_STOPWORDS
from catia_manual.gui.style import ASSETS

TERMS_ROLE = Qt.ItemDataRole.UserRole + 3
READER_FOCUS = (ASSETS / "reader_focus.js").read_text("utf-8")


def focus_terms(original, expanded):
    tokens = re.findall(r"[\u3400-\u9fff]+|[a-zA-Z0-9]+", original + " " + expanded)
    return list(
        dict.fromkeys(t.casefold() for t in tokens if t.casefold() not in ENGLISH_STOPWORDS)
    )


def rich_matches(text, terms):
    terms = sorted({t for t in terms if t}, key=len, reverse=True)
    if not terms:
        return html.escape(text)
    pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    parts, end = [], 0
    for match in pattern.finditer(text):
        parts.extend(
            (
                html.escape(text[end : match.start()]),
                '<span style="background:#ffe7a0;color:#40340f;font-weight:600">'
                + html.escape(match.group())
                + "</span>",
            )
        )
        end = match.end()
    return "".join(parts) + html.escape(text[end:])


def snippet_text(text, terms):
    """Replace FTS's legacy bracket emphasis only around query matches."""
    return re.sub(
        r"\[([^\[\]]+)\]",
        lambda m: m[1] if any(t in m[1].casefold() for t in terms) else m[0],
        text,
    )


class MatchDelegate(QStyledItemDelegate):
    def __init__(self, parent, *, tree=False):
        super().__init__(parent)
        self.tree = tree
        self.terms = []

    def document(self, option, index):
        text = index.data() or ""
        terms = index.data(TERMS_ROLE) if not self.tree else self.terms
        rows = text.splitlines()
        title = rich_matches(rows[0] if rows else "", terms or [])
        if self.tree:
            markup = title
        else:
            markup = '<div style="font-weight:600;margin-bottom:5px">' + title + "</div>"
            if len(rows) > 1:
                markup += (
                    '<div style="color:#637d8b;font-size:9pt;margin-bottom:7px">'
                    + rich_matches(rows[1], terms or [])
                    + "</div>"
                )
            if len(rows) > 2:
                markup += (
                    '<div style="color:#536773">'
                    + rich_matches(" ".join(rows[2:]), terms or [])
                    + "</div>"
                )
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setDocumentMargin(0)
        doc.setHtml(markup)
        return doc

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        doc = self.document(opt, index)
        style = opt.widget.style()
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.left(), rect.top() + (4 if not self.tree else 0))
        doc.setTextWidth(-1 if self.tree else rect.width())
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(
            QPalette.ColorRole.Text, opt.palette.color(QPalette.ColorRole.Text)
        )
        doc.documentLayout().draw(painter, context)
        painter.restore()

    def sizeHint(self, option, index):
        if self.tree:
            return super().sizeHint(option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        doc = self.document(opt, index)
        doc.setTextWidth(max(160, self.parent().viewport().width() - 24))
        return QSize(180, int(doc.size().height()) + 28)


class SearchFocus:
    def update_search_focus(self):
        location = self.history.current
        terms = (location.search_terms if location else None) or []
        enabled = self.focus_action.isChecked()
        self.focus_action.setEnabled(bool(terms))
        self.application_script(
            READER_FOCUS + "\nwindow.cmvFocus(" + json.dumps(terms if enabled else []) + ");"
        )
