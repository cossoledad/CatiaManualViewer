/* Runs only in Qt's isolated ApplicationWorld. Original document scripts stay disabled. */
(() => {
  const key = document.body.dataset.pageKey;
  if (window.cmvReader && window.cmvReader.key === key) return;
  const nodes = [...document.querySelectorAll('.cmv-unit[data-unit]')];
  const byId = new Map(nodes.map(node => [node.dataset.unit, node]));
  let lastErrors = new Set();
  function capture() {
    const node = nodes.find(node => {
      const rect = node.getBoundingClientRect();
      return rect.width && rect.bottom > 0 && rect.top < innerHeight;
    });
    return {id: node ? node.dataset.unit : '',
      offset: node ? node.getBoundingClientRect().top : 0, x: scrollX, y: scrollY};
  }
  function restore(position) {
    if (!position) return;
    if (position.fragment) {
      const target = document.getElementById(position.fragment)
        || document.getElementsByName(position.fragment)[0];
      if (target) target.scrollIntoView();
      return;
    }
    const node = byId.get(position.id);
    if (node && node.getClientRects().length) {
      scrollBy(0, node.getBoundingClientRect().top - position.offset);
    } else {
      scrollTo(position.x || 0, position.y || 0);
    }
  }
  window.cmvReader = {key, capture, restore,
    visible() {
      return nodes.filter(node => {
        const rect = node.getBoundingClientRect();
        return rect.width && rect.bottom > -100 && rect.top < innerHeight + 250;
      }).map(node => node.dataset.unit);
    },
    apply(expected, payload) {
      if (expected !== key || document.body.dataset.pageKey !== key) return false;
      const position = payload.position || capture();
      if (payload.mode) document.body.dataset.mode = payload.mode;
      for (const [id, value] of Object.entries(payload.results || {})) {
        const node = byId.get(id);
        if (!node) continue;
        const target = node.querySelector(':scope > .cmv-translated');
        if (target.textContent !== value) target.textContent = value;
        node.dataset.state = 'done';
      }
      if (payload.errors) for (const id of new Set([...lastErrors, ...Object.keys(payload.errors)])) {
        const node = byId.get(id);
        if (!node || node.dataset.state === 'done') continue;
        const error = payload.errors[id];
        node.dataset.state = error ? 'error' : '';
        node.querySelector(':scope > .cmv-retry').title = error || '';
      }
      if (payload.errors) lastErrors = new Set(Object.keys(payload.errors));
      restore(position);
      return true;
    }
  };
})();
