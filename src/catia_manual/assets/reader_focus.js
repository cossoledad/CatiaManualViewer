/* Paint text ranges; keep the source DOM and translation text intact. */
(() => {
  if (window.cmvFocus) return;
  const style = document.createElement('style');
  style.textContent = '::highlight(cmv-search){background:#ffe7a0;color:#40340f}';
  document.head.appendChild(style);
  window.cmvFocus = terms => {
    if (!window.CSS || !CSS.highlights) return 0;
    CSS.highlights.delete('cmv-search');
    if (!terms.length) return 0;
    const escaped = terms.filter(Boolean).sort((a,b) => b.length-a.length)
      .map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    if (!escaped.length) return 0;
    const pattern = new RegExp(escaped.join('|'), 'giu');
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const ranges = [];
    let node;
    while ((node = walker.nextNode()) && ranges.length < 3000) {
      if (node.parentElement.closest('script,style,.cmv-retry')) continue;
      pattern.lastIndex = 0;
      for (const match of node.textContent.matchAll(pattern)) {
        const range = new Range();
        range.setStart(node, match.index);
        range.setEnd(node, match.index + match[0].length);
        ranges.push(range);
        if (ranges.length >= 3000) break;
      }
    }
    CSS.highlights.set('cmv-search', new Highlight(...ranges));
    return ranges.length;
  };
})();
