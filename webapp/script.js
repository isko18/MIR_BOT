/**
 * Telegram Mini App — выбор счёта букмекера.
 * Ожидается window.Telegram.WebApp из telegram-web-app.js
 */

const ACCOUNTS = [
  {
    id: "1xbet",
    label: "1XBET",
    logo: "https://placehold.co/72x72/2e7d32/ffffff/png?text=1x",
  },
  {
    id: "1win",
    label: "1WIN",
    logo: "https://placehold.co/72x72/1565c0/ffffff/png?text=1W",
  },
  {
    id: "melbet",
    label: "MELBET",
    logo: "https://placehold.co/72x72/ff6f00/ffffff/png?text=M",
  },
  {
    id: "mostbet",
    label: "MOSTBET",
    logo: "https://placehold.co/72x72/c62828/ffffff/png?text=MB",
  },
  {
    id: "winwin",
    label: "WINWIN",
    logo: "https://placehold.co/72x72/6a1b9a/ffffff/png?text=WW",
  },
  {
    id: "888starz",
    label: "888STARZ",
    logo: "https://placehold.co/72x72/f9a825/1a1a1a/png?text=888",
  },
];

function getTelegramWebApp() {
  return window.Telegram?.WebApp ?? null;
}

function applyThemeFromTelegram(tg) {
  if (!tg?.themeParams) return;
  const p = tg.themeParams;
  const root = document.documentElement;
  if (p.bg_color) root.style.setProperty("--tg-bg", p.bg_color);
  if (p.secondary_bg_color) {
    root.style.setProperty("--tg-bg-secondary", p.secondary_bg_color);
  }
  if (p.text_color) root.style.setProperty("--tg-text", p.text_color);
  if (p.hint_color) root.style.setProperty("--tg-hint", p.hint_color);
}

function initTelegram() {
  const tg = getTelegramWebApp();
  if (!tg) {
    console.warn("Telegram.WebApp недоступен (откройте внутри Telegram)");
    return null;
  }
  tg.ready();
  tg.expand();
  applyThemeFromTelegram(tg);
  tg.onEvent?.("themeChanged", () => applyThemeFromTelegram(tg));
  return tg;
}

function sendSelection(tg, accountId) {
  const payload = `selected:${accountId}`;
  if (tg?.sendData) {
    tg.sendData(payload);
  } else {
    console.log("[dev] sendData:", payload);
  }
}

function sendCancel(tg) {
  const payload = "cancel";
  if (tg?.sendData) {
    tg.sendData(payload);
  } else {
    console.log("[dev] sendData:", payload);
  }
}

function createAccountButton(account, tg) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn-account";
  btn.dataset.accountId = account.id;

  const img = document.createElement("img");
  img.className = "btn-account__logo";
  img.src = account.logo;
  img.alt = "";
  img.width = 36;
  img.height = 36;
  img.loading = "lazy";
  img.decoding = "async";

  const span = document.createElement("span");
  span.className = "btn-account__label";
  span.textContent = account.label;

  btn.append(img, span);

  btn.addEventListener("click", () => sendSelection(tg, account.id));

  return btn;
}

function renderAccountGrid(container, tg) {
  container.replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const acc of ACCOUNTS) {
    fragment.appendChild(createAccountButton(acc, tg));
  }
  container.appendChild(fragment);
}

function bindCancel(tg) {
  const el = document.getElementById("btn-cancel");
  if (!el) return;
  el.addEventListener("click", () => sendCancel(tg));
}

function main() {
  const tg = initTelegram();
  const grid = document.getElementById("account-grid");
  if (!grid) return;

  renderAccountGrid(grid, tg);
  bindCancel(tg);
}

main();
