// 오프라인에서도 사이트를 볼 수 있도록 하는 서비스 워커.
// 페이지(HTML)는 네트워크 우선(온라인이면 항상 최신 반영, 오프라인이거나 3초 넘게 응답이 없으면 캐시 사용),
// 아바타·부스컷 이미지는 캐시 우선(한 번 본 이미지는 이후 오프라인에서도 계속 보임)으로 동작함.
// 또 페이지가 보내준 이미지 목록(precache-images 메시지, 부스컷 썸네일·프로필 사진)을 백그라운드에서 미리 받아 두어서,
// 처음 접속한 뒤에는 보지 않은 부스의 사진도 오프라인에서 보이게 함.
// 이미지를 같은 파일명으로 교체했을 때 바로 반영되게 하려면 CACHE_VERSION 을 올리면 됨.
const CACHE_VERSION = "v5";
const CACHE_NAME = `furstclass-cache-${CACHE_VERSION}`;
const SHELL_URLS = ["./", "./index.html", "./favicon.png"];
// 저장해 둔 페이지가 있을 때 네트워크를 기다리는 최대 시간(이 시간이 지나면 저장해 둔 페이지로 먼저 띄움)
const NETWORK_TIMEOUT_MS = 3000;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin || event.request.method !== "GET") return;

  const isImage = url.pathname.includes("/avatars/") || url.pathname.includes("/booth-cuts/");

  if (isImage) {
    // 캐시 우선: 이미 받아둔 아바타/부스컷은 오프라인에서도 바로 표시
    event.respondWith(
      // 미리 받아 둔 이미지(precacheImages)와 요청 헤더가 달라도 찾을 수 있게 ignoreVary
      caches.match(event.request, { ignoreVary: true }).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          // 아직 올리지 않은 부스컷(404)은 캐시하지 않아야 나중에 파일을 넣었을 때 바로 보임
          if (!response.ok) return response;
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        }).catch(() => cached);
      })
    );
    return;
  }

  // 그 외(페이지 본문 등)는 네트워크 우선: 온라인이면 최신 내용, 오프라인이면 캐시로 대체.
  // 다만 행사장처럼 회선이 끊기지는 않고 아주 느리기만 하면, 캐시가 있어도 응답이 올 때까지 빈 화면이었음.
  // 그래서 저장해 둔 페이지가 있으면 네트워크를 최대 NETWORK_TIMEOUT_MS 만 기다리고 그 뒤엔 캐시로 먼저 띄움.
  // 늦게라도 받은 최신 페이지는 캐시에 넣어 두어 다음 접속 때 반영됨 (2026-10-08).
  // 오류 응답(404·500 등)은 캐시에 넣지 않아, 잠깐 난 오류 페이지가 오프라인용으로 남지 않게 함.
  let saved = Promise.resolve();
  const network = fetch(event.request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      saved = caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
    }
    return response;
  });
  // 캐시로 먼저 띄운 뒤에도 서비스 워커가 최신 페이지를 다 받아 저장할 때까지 살아 있게 함
  event.waitUntil(network.then(() => saved).catch(() => {}));
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const offlineFallback = () => cached || caches.match("./index.html").then((page) => page || Response.error());
      if (!cached) return network.catch(offlineFallback);
      const timeout = new Promise((resolve) => setTimeout(() => resolve(cached), NETWORK_TIMEOUT_MS));
      return Promise.race([network.catch(offlineFallback), timeout]);
    })
  );
});

// 페이지(index.html)가 보내준 목록용 부스컷 썸네일·프로필 사진을 미리 받아 캐시에 넣음.
// 이미 캐시에 있는 이미지는 건너뛰므로 두 번째 접속부터는 새로 추가된 사진만 받음.
const PRECACHE_CONCURRENCY = 4;

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type !== "precache-images" || !Array.isArray(data.urls)) return;
  event.waitUntil(precacheImages(data.urls));
});

async function precacheImages(urls) {
  const cache = await caches.open(CACHE_NAME);
  const queue = urls.filter((u) => {
    try { return new URL(u).origin === location.origin; } catch { return false; }
  });
  let offline = false;
  const worker = async () => {
    while (queue.length && !offline) {
      const url = queue.shift();
      if (await cache.match(url, { ignoreVary: true })) continue;
      try {
        const response = await fetch(url);
        // 없는 파일(404)은 캐시하지 않음 (나중에 파일을 올리면 바로 보이도록)
        if (response.ok) await cache.put(url, response);
      } catch {
        // 받는 도중 인터넷이 끊기면 멈추고, 다음 접속 때 남은 것부터 이어서 받음
        offline = true;
      }
    }
  };
  await Promise.all(Array.from({ length: PRECACHE_CONCURRENCY }, worker));
}
