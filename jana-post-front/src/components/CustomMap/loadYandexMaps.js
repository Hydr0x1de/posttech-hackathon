let loadPromise = null;

export function loadYandexMaps(timeoutMs = 6000) {
  if (loadPromise) return loadPromise;

  loadPromise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('yandex_maps_timeout')), timeoutMs);

    if (window.ymaps) {
      window.ymaps.ready(() => {
        clearTimeout(timer);
        resolve(window.ymaps);
      });
      return;
    }

    const apiKey = import.meta.env.VITE_YANDEX_MAPS_API_KEY;
    const script = document.createElement('script');
    script.src = `https://api-maps.yandex.ru/2.1/?apikey=${encodeURIComponent(apiKey || '')}&lang=ru_RU`;
    script.async = true;
    script.onload = () => {
      if (!window.ymaps) {
        clearTimeout(timer);
        reject(new Error('yandex_maps_not_available'));
        return;
      }
      window.ymaps.ready(() => {
        clearTimeout(timer);
        resolve(window.ymaps);
      });
    };
    script.onerror = () => {
      clearTimeout(timer);
      reject(new Error('yandex_maps_load_failed'));
    };
    document.head.appendChild(script);
  }).catch((error) => {
    loadPromise = null;
    throw error;
  });

  return loadPromise;
}
