async function show() {
  const {instance} = await chrome.runtime.sendMessage({type: "qorgau-identity"});
  document.querySelector("#extension").textContent = chrome.runtime.id;
  document.querySelector("#instance").textContent = instance;
}
show();
