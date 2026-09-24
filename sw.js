// 呢個service worker淨係為咗滿足瀏覽器「可安裝到主畫面」嘅技術要求先存在，
// 冇做任何緩存（caching）——所有資料/音頻都照舊直接上網攞最新嘅，唔會因為
// 用咗service worker而出現「睇到舊版本」嘅情況。
self.addEventListener("install", function (event) {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function (event) {
  event.respondWith(fetch(event.request));
});
