/* Keep remote-access credentials in session storage, never in requests or history. */
(() => {
"use strict";
const tokenPrefix = "#token=";
let fragmentToken = null;
if (location.hash.startsWith(tokenPrefix)) {
  try {
    fragmentToken = decodeURIComponent(location.hash.slice(tokenPrefix.length));
  } catch (_) {
    fragmentToken = null;
  }
}
if (fragmentToken) {
  sessionStorage.setItem("ryujin-lcd-token", fragmentToken);
  history.replaceState(null, "", location.pathname + location.search);
}
window.ryujinAuthHeaders = () => {
  const token = sessionStorage.getItem("ryujin-lcd-token");
  return token ? { Authorization: `Bearer ${token}` } : {};
};
})();
