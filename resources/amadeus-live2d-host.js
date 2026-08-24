if (
  new URLSearchParams(location.search).get('host') === 'tauri' ||
  window.frameElement?.id === 'live2d-frame'
) {
    document.documentElement.dataset.host = 'tauri';
  }
