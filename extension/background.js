let connection = null;
let active = false;
let latest = null;
let binding = null;
let browserInstance = null;
let identityPromise = null;
function identify() {
  if (!identityPromise) identityPromise = loadIdentity();
  return identityPromise;
}
async function loadIdentity() {
  const saved = await chrome.storage.local.get("qorgauBrowserInstance");
  browserInstance = saved.qorgauBrowserInstance || crypto.randomUUID();
  if (!saved.qorgauBrowserInstance) await chrome.storage.local.set({qorgauBrowserInstance: browserInstance});
}
chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message.type !== "qorgau-identity") return;
  identify().then(() => respond({instance: browserInstance}));
  return true;
});
function send(message) {
  if (connection && browserInstance) connection.postMessage({...message, browser_instance: browserInstance});
}
function connect() {
  if (!browserInstance) { identify().then(connect); return; }
  if (connection) return;
  connection = chrome.runtime.connectNative("kz.qorgau.agent");
  connection.onMessage.addListener((message) => {
    const wasActive = active;
    if (binding !== message.binding) latest = null;
    binding = message.binding || null;
    active = !!message.active;
    if (!active) latest = null;
    if (active && !wasActive) observe().catch(() => {});
    chrome.action.setBadgeText({
      text: active ? (message.access === "LOCKED" ? "!" : "ON") : "",
    });
    chrome.action.setBadgeBackgroundColor({
      color: message.access === "LOCKED" ? "#bf5450" : "#247455",
    });
    chrome.action.setTitle({
      title: active
        ? "Qorgau: наблюдение идёт, системная блокировка недоступна"
        : message.error === "BROWSER_PROFILE_MISMATCH"
          ? "Qorgau: этот профиль браузера не привязан к агенту"
          : "Qorgau: сеанс не идёт",
    });
  });
  connection.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;
    connection = null;
    active = false;
    chrome.action.setBadgeText({ text: "?" });
    chrome.action.setTitle({ title: "Qorgau: нет связи с локальным агентом" });
  });
  send({ type: "status" });
}
async function observe() {
  if (!connection) connect();
  if (!connection) return;
  if (!active) {
    send({ type: "status" });
    return;
  }
  const window = await chrome.windows.getLastFocused();
  const tabs = await chrome.tabs.query({ active: true, windowId: window.id });
  const tab = tabs[0];
  if (!tab) return;
  const observation = { url: tab.url || "", focused: window.focused };
  const key = JSON.stringify(observation);
  if (key !== latest) {
    latest = key;
    send({ type: "observation", observation, binding });
  } else send({ type: "status" });
}
chrome.tabs.onActivated.addListener(() => observe().catch(() => {}));
chrome.tabs.onUpdated.addListener((_id, change, tab) => {
  if (tab.active && change.url) observe().catch(() => {});
});
chrome.windows.onFocusChanged.addListener(() => observe().catch(() => {}));
chrome.alarms.create("qorgau-heartbeat", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => observe().catch(() => {}));
chrome.runtime.onStartup.addListener(() => connect());
chrome.action.onClicked.addListener(() => observe().catch(() => {}));
connect();
