"""Small script-free browser reader for the already authorized manual service."""

import html
import re

from catia_manual.core.sharing import link_for_source

HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
}


def reader_html(document, source):
    uri = html.escape(link_for_source(source.to_dict()), quote=True)
    title = html.escape(document.title)
    parts, anchors = [], set()
    for block in document.blocks:
        tag = block.kind if re.fullmatch(r"h[1-6]|pre", block.kind) else "p"
        anchor = ""
        if block.fragment and block.fragment not in anchors:
            anchor = '<span id="' + html.escape(block.fragment, quote=True) + '"></span>'
            anchors.add(block.fragment)
        parts.append(
            anchor + f'<{tag} id="block-{block.id}">' + html.escape(block.text) + f"</{tag}>"
        )
    if source.fragment and source.fragment not in anchors:
        parts.insert(0, '<span id="' + html.escape(source.fragment, quote=True) + '"></span>')
    # A server-side reader has no access to the desktop's translated DOM or local resources.
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{font:16px/1.75 system-ui,sans-serif;max-width:960px;margin:32px auto;
padding:0 24px;color:#23394d;background:#f7fafc}}header{{padding:16px 0;border-bottom:1px solid
#dce5ed}}a{{color:#167c85}}article{{background:white;padding:24px;border-radius:12px}}
h1,h2,h3{{line-height:1.4}}input{{box-sizing:border-box;width:100%;padding:8px;border:1px solid
#dce5ed;border-radius:6px}}pre{{white-space:pre-wrap}}span:target,p:target{{background:#fff3c4}}
small{{color:#60758a}}</style><header><strong>Catia Manual Viewer</strong> · 分享阅读
<p><a href="{uri}">在桌面查看器中打开</a></p><input aria-label="分享标识" readonly value="{uri}">
<p><small>原文文字视图 · 图片、示例及翻译请在桌面查看器中阅读</small></p></header>
<h1>{title}</h1><article>{"".join(parts)}</article></html>'''
