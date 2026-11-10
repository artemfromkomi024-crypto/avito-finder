// Этот service worker НИЧЕГО не кэширует.
// Его задача — обновить старые версии у пользователей: почистить кэш,
// снять себя с регистрации и перезагрузить страницу.

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    } catch (e) {}

    try {
      await self.registration.unregister();
    } catch (e) {}

    try {
      const clients = await self.clients.matchAll({ type: 'window' });
      for (const c of clients) {
        try { c.navigate(c.url); } catch (e) {}
      }
    } catch (e) {}
  })());
});

// никакого fetch-обработчика: все запросы идут напрямую в сеть
