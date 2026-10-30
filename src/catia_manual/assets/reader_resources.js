/* ApplicationWorld only: resource gestures never execute the manual's scripts. */
(() => {
  const key = document.body.dataset.pageKey;
  if (window.cmvResourceKey === key) return;
  window.cmvResourceKey = key;
  let bridge = null;
  const pending = [];
  new QWebChannel(qt.webChannelTransport, channel => {
    bridge = channel.objects.resources;
    for (const [action, target] of pending) bridge.activate(key, action, target);
    pending.length = 0;
  });
  function send(action, target) {
    if (bridge) bridge.activate(key, action, target);
    else pending.push([action, target]);
  }
  document.addEventListener('click', event => {
    const anchor = event.target.closest('a[data-cmv-kind="sample"]');
    if (anchor) {
      event.preventDefault(); event.stopPropagation();
      if (event.detail === 0) send('open', anchor.href);
      else if (event.detail === 1) send('select', anchor.href);
      return;
    }
    const image = event.target.closest('img[data-cmv-image]');
    const link = event.target.closest('a[href]');
    if (image && !link) {
      event.preventDefault(); send('image', image.src);
    } else if (link && link.dataset.cmvKind === 'image') {
      event.preventDefault(); send('image', link.href);
    }
  }, true);
  document.addEventListener('dblclick', event => {
    const anchor = event.target.closest('a[data-cmv-kind="sample"]');
    if (anchor) {
      event.preventDefault(); event.stopPropagation(); send('open', anchor.href);
    }
  }, true);
})();
