let connection = null;
let active = false;
let latest = null;
function connect() {
  if (connection) return;
  connection = chrome.runtime.connectNative("kz.qorgau.agent");
  connection.onMessage.addListener((message) => {
    const wasActive = active;
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
  connection.postMessage({ type: "status" });
}
async function observe() {
  if (!connection) connect();
  if (!connection) return;
  if (!active) {
    connection.postMessage({ type: "status" });
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
    connection.postMessage({ type: "observation", observation });
  } else connection.postMessage({ type: "status" });
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
