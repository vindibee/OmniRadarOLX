/**
 * Экраны Mini App: онбординг → личный кабинет (подписка, поиск, фильтры, история).
 *
 * Состояние одно (`state`), перерисовка — из него. Никаких фреймворков: экранов немного,
 * а дешёвая загрузка внутри Telegram важнее удобства разработчика.
 */
const tg = window.Telegram?.WebApp;

// Столько же локаций принимает бэкенд (app/services/parsers/olx_ua.py).
const MAX_LOCATIONS = 5;

const state = {
  me: null,
  catalog: null,
  status: null,
  filters: [],
  favorites: [],
  // Локации, выбранные в форме поиска: их можно добавить несколько.
  pickedLocations: [],
  tab: "subscription",
  openFilter: null,
};

const $ = (selector) => document.querySelector(selector);
const screens = ["loading", "language", "onboarding", "main"];

function showScreen(name) {
  screens.forEach((screen) => {
    $(`#screen-${screen}`).classList.toggle("hidden", screen !== name);
  });
}

function showTab(tab) {
  state.tab = tab;
  ["subscription", "search", "filters", "favorites", "history", "admin"].forEach((name) => {
    $(`#tab-${name}`).classList.toggle("hidden", name !== tab);
  });
  document.querySelectorAll("nav [data-tab]").forEach((button) => {
    button.classList.toggle("text-link", button.dataset.tab === tab);
    button.classList.toggle("text-muted", button.dataset.tab !== tab);
  });
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(node.dataset.timer);
  node.dataset.timer = setTimeout(() => node.classList.add("hidden"), 2500);
}

function haptic(type = "light") {
  tg?.HapticFeedback?.impactOccurred?.(type);
}

function formatDateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString(i18n.lang, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatPrice(item) {
  if (item.price === null || item.price === undefined) return "";
  const amount = Number(item.price).toLocaleString(i18n.lang, { maximumFractionDigits: 0 });
  return `${amount} ${item.currency || ""}`.trim();
}

// ---------- Онбординг ----------

function renderLanguageScreen() {
  const list = $("#language-list");
  list.innerHTML = "";
  LANGUAGES.forEach(({ code, label, flag }) => {
    const button = document.createElement("button");
    button.className =
      "tap flex w-full items-center gap-3 rounded-2xl bg-card px-4 py-4 text-left text-base";
    button.innerHTML = `<span class="text-xl">${flag}</span><span>${label}</span>`;
    button.onclick = async () => {
      haptic();
      i18n.use(code);
      i18n.apply();
      await api.setLanguage(code);
      state.me.language_code = code;
      renderOnboarding();
      showScreen("onboarding");
    };
    list.append(button);
  });
  i18n.apply();
}

function renderOnboarding() {
  const steps = $("#onboarding-steps");
  steps.innerHTML = "";
  ["onboarding.step1", "onboarding.step2", "onboarding.step3"].forEach((key, index) => {
    const item = document.createElement("li");
    item.className = "flex gap-3";
    item.innerHTML = `
      <span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-card text-sm">${index + 1}</span>
      <span class="pt-0.5">${i18n.t(key)}</span>`;
    steps.append(item);
  });

  const trialButton = $("#trial-button");
  trialButton.classList.toggle("hidden", !state.me.access.trial_available);
  trialButton.onclick = async () => {
    haptic("medium");
    try {
      state.me.access = await api.startTrial();
      toast(i18n.t("toast.trial"));
      await openMain();
    } catch (error) {
      toast(error.message || i18n.t("error.generic"));
    }
  };
  $("#skip-onboarding").onclick = () => openMain();
  i18n.apply();
}

// ---------- Прозрачность ----------

function humanAgo(seconds) {
  if (seconds === null || seconds === undefined) return null;
  return seconds < 90
    ? i18n.t("time.seconds", { value: seconds })
    : i18n.t("time.minutes", { value: Math.round(seconds / 60) });
}

function renderStatus() {
  const widget = $("#status-widget");
  const status = state.status;
  if (!status) {
    widget.classList.add("hidden");
    return;
  }
  widget.classList.remove("hidden");
  const ago = humanAgo(status.seconds_ago);
  const line = ago ? i18n.t("status.lastRun", { ago }) : i18n.t("status.never");
  widget.innerHTML = `
    <div class="font-semibold">${status.is_running ? i18n.t("status.active") : i18n.t("status.idle")}</div>
    <div class="mt-1 text-sm text-muted">${line}</div>
    <div class="text-sm text-muted">${i18n.t("status.foundToday", { count: status.found_today })}</div>`;
}

async function refreshStatus() {
  try {
    state.status = await api.status();
  } catch (error) {
    state.status = null; // статус — украшение, из-за него экран падать не должен
  }
  renderStatus();
}

// ---------- Подписка ----------

function renderAccess() {
  const { access } = state.me;
  const card = $("#access-card");
  if (access.is_admin) {
    card.innerHTML = `
      <div class="flex items-center gap-3">
        <span class="text-2xl">🛠</span>
        <div class="font-semibold">${i18n.t("admin.unlimited")}</div>
      </div>`;
    return;
  }
  const title = access.is_trial
    ? i18n.t("access.trial")
    : access.is_allowed
      ? i18n.t("access.active")
      : i18n.t("access.inactive");
  const hint = access.is_allowed
    ? `${i18n.t("access.until")} ${formatDateTime(access.until)}`
    : access.trial_available
      ? i18n.t("access.trialHint")
      : i18n.t("access.buyHint");

  card.innerHTML = `
    <div class="flex items-center gap-3">
      <span class="text-2xl">${access.is_allowed ? "✅" : "⏳"}</span>
      <div>
        <div class="font-semibold">${title}</div>
        <div class="text-sm text-muted">${hint}</div>
      </div>
    </div>`;

  if (!access.is_allowed && access.trial_available) {
    const button = document.createElement("button");
    button.className = "tap mt-4 w-full rounded-xl bg-accent px-4 py-3 font-semibold text-onaccent";
    button.textContent = i18n.t("onboarding.trial");
    button.onclick = async () => {
      state.me.access = await api.startTrial();
      toast(i18n.t("toast.trial"));
      renderAccess();
    };
    card.append(button);
  }
}

function renderTariffs() {
  const list = $("#tariff-list");
  list.innerHTML = "";
  // Владельцу прайс не нужен — у него доступ и так бессрочный.
  if (state.me.access.is_admin) return;
  state.me.offers.forEach((offer) => {
    const card = document.createElement("div");
    card.className = "rounded-2xl bg-card p-4";
    const discount =
      offer.discount_percent > 0
        ? `<span class="ml-2 rounded-full bg-accent px-2 py-0.5 text-xs text-onaccent">${i18n.t(
            "tariff.discount",
            { percent: offer.discount_percent },
          )}</span>`
        : "";
    card.innerHTML = `
      <div class="flex items-baseline justify-between">
        <div class="font-semibold">${i18n.t(`tariff.${offer.tariff}`)}${discount}</div>
        <div class="text-sm text-muted">⭐ ${offer.price_stars} · $${offer.price_usd}</div>
      </div>`;

    const buttons = document.createElement("div");
    buttons.className = "mt-3 flex gap-2";
    if (state.me.payment_methods.includes("stars")) {
      buttons.append(payButton(i18n.t("pay.stars"), () => payWithStars(offer.tariff)));
    }
    if (state.me.payment_methods.includes("cryptobot")) {
      buttons.append(payButton(i18n.t("pay.crypto"), () => payWithCrypto(offer.tariff)));
    }
    card.append(buttons);
    list.append(card);
  });
}

function payButton(label, onClick) {
  const button = document.createElement("button");
  button.className = "tap flex-1 rounded-xl bg-accent px-3 py-2.5 text-sm font-semibold text-onaccent";
  button.textContent = label;
  button.onclick = onClick;
  return button;
}

async function payWithStars(tariff) {
  haptic("medium");
  try {
    const invoice = await api.starsInvoice(tariff);
    // Telegram сам показывает окно оплаты; зачисление придёт боту вебхуком.
    tg.openInvoice(invoice.url, async (status) => {
      if (status !== "paid") return;
      toast(i18n.t("toast.paid"));
      await refreshMe();
      renderAccess();
    });
  } catch (error) {
    toast(error.message || i18n.t("error.generic"));
  }
}

async function payWithCrypto(tariff) {
  haptic("medium");
  try {
    const invoice = await api.cryptoInvoice(tariff);
    tg.openTelegramLink(invoice.url);
  } catch (error) {
    toast(error.message || i18n.t("error.generic"));
  }
}

// ---------- Поиск и фильтры ----------

function option(value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  return node;
}

function fillSelect(select, items, placeholderKey) {
  select.innerHTML = "";
  select.append(option("", i18n.t(placeholderKey)));
  items.forEach((item) => select.append(option(item.id, item.names[i18n.lang] || item.names.uk)));
}

/** Города и подкатегории зависят от выбора выше: показываем только подходящие. */
function renderCatalogSelects() {
  if (!state.catalog) return;
  const regions = state.catalog.regions;
  const categories = state.catalog.categories;
  const regionSelect = $("#select-region");
  const citySelect = $("#select-city");
  const categorySelect = $("#select-category");
  const subcategorySelect = $("#select-subcategory");

  fillSelect(regionSelect, regions, "search.anyRegion");
  fillSelect(citySelect, [], "search.anyCity");
  fillSelect(categorySelect, categories, "search.anyCategory");
  fillSelect(subcategorySelect, [], "search.anySubcategory");

  regionSelect.onchange = () => {
    const region = regions.find((item) => String(item.id) === regionSelect.value);
    fillSelect(citySelect, region?.children || [], "search.anyCity");
  };
  $("#add-location").onclick = () => addPickedLocation(regions, regionSelect, citySelect);
  renderLocationChips();
  categorySelect.onchange = () => {
    const category = categories.find((item) => String(item.id) === categorySelect.value);
    fillSelect(subcategorySelect, category?.children || [], "search.anySubcategory");
  };
}

/** Добавляет выбранную пару «область/город» в список локаций фильтра. */
function addPickedLocation(regions, regionSelect, citySelect) {
  const region = regions.find((item) => String(item.id) === regionSelect.value);
  if (!region) return;
  const city = (region.children || []).find((item) => String(item.id) === citySelect.value);
  const picked = city
    ? { kind: "city", id: city.id, name: city.names[i18n.lang] || city.names.uk }
    : { kind: "region", id: region.id, name: region.names[i18n.lang] || region.names.uk };

  if (state.pickedLocations.some((item) => item.kind === picked.kind && item.id === picked.id)) {
    return;
  }
  if (state.pickedLocations.length >= MAX_LOCATIONS) return;
  state.pickedLocations.push(picked);
  haptic();
  renderLocationChips();
}

function renderLocationChips() {
  const box = $("#location-chips");
  box.innerHTML = "";
  state.pickedLocations.forEach((location, index) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "tap rounded-full bg-card px-3 py-1.5 text-sm";
    chip.textContent = `${location.name} ✕`;
    chip.onclick = () => {
      state.pickedLocations.splice(index, 1);
      renderLocationChips();
    };
    box.append(chip);
  });
}

function bindSearchForm() {
  $("#search-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const number = (key) => {
      const value = (form.get(key) || "").toString().trim();
      return value ? Number(value) : null;
    };
    // Подкатегория точнее категории; город точнее области.
    const categoryId = number("category_id") ?? number("category");

    const payload = {
      name: (form.get("name") || "").toString().trim() || null,
      marketplace: state.me.marketplaces[0]?.code,
      criteria: {
        query: form.get("query"),
        price_min: form.get("price_min") || null,
        price_max: form.get("price_max") || null,
        condition: form.get("condition") || null,
        category_id: categoryId,
        locations: state.pickedLocations,
        minus_words: (form.get("minus_words") || "")
          .toString()
          .split(",")
          .map((word) => word.trim())
          .filter(Boolean),
        match_all_words: form.get("match_all_words") === "on",
        only_private: form.get("only_private") === "on",
        only_with_delivery: form.get("only_with_delivery") === "on",
        only_with_photo: form.get("only_with_photo") === "on",
      },
    };

    try {
      await api.createFilter(payload);
      event.target.reset();
      state.pickedLocations = [];
      renderLocationChips();
      toast(i18n.t("toast.saved"));
      haptic("medium");
      await loadFilters();
      showTab("filters");
    } catch (error) {
      toast(error.status === 402 ? i18n.t("toast.needSubscription") : error.message);
    }
  };
}

async function loadFilters() {
  state.filters = await api.filters();
  renderFilters();
}

function renderFilters() {
  const list = $("#filter-list");
  list.innerHTML = "";
  if (state.filters.length === 0) {
    list.innerHTML = `<p class="text-muted">${i18n.t("filters.empty")}</p>`;
    return;
  }

  state.filters.forEach((item) => {
    const card = document.createElement("div");
    card.className = "rounded-2xl bg-card p-4";
    card.innerHTML = `
      <div class="font-semibold">${item.title}</div>
      <div class="mt-1 text-sm text-muted">${describeCriteria(item.criteria)}</div>
      <div class="mt-1 text-sm text-muted">
        ${item.is_active ? i18n.t("filters.active") : i18n.t("filters.paused")}
      </div>`;

    const actions = document.createElement("div");
    actions.className = "mt-3 flex gap-2 text-sm";
    actions.append(
      smallButton(i18n.t("filters.history"), () => openHistory(item)),
      smallButton(item.is_active ? i18n.t("filters.pause") : i18n.t("filters.resume"), async () => {
        await api.toggleFilter(item.id);
        await loadFilters();
      }),
      smallButton(i18n.t("filters.delete"), async () => {
        await api.deleteFilter(item.id);
        toast(i18n.t("toast.deleted"));
        await loadFilters();
      }),
    );
    card.append(actions);
    list.append(card);
  });
}

/** Человеческое описание фильтра: города и категории — названиями, не числами. */
function describeCriteria(criteria) {
  const parts = [criteria.query];
  if (criteria.price_min || criteria.price_max) {
    parts.push(`${criteria.price_min || "…"} – ${criteria.price_max || "…"}`);
  }
  if (criteria.condition) {
    parts.push(i18n.t(`search.state${criteria.condition === "new" ? "New" : "Used"}`));
  }
  (criteria.locations || []).forEach((location) => parts.push(location.name || location.id));
  const category = findCatalogName(state.catalog?.categories, criteria.category_id);
  if (category) parts.push(category);
  if (criteria.only_private) parts.push(i18n.t("search.onlyPrivate"));
  if (criteria.only_with_delivery) parts.push(i18n.t("search.onlyDelivery"));
  if (criteria.only_with_photo) parts.push(i18n.t("search.onlyPhoto"));
  if (criteria.minus_words?.length) parts.push(`− ${criteria.minus_words.join(", ")}`);
  return parts.join(" · ");
}

function findCatalogName(groups, id) {
  if (!groups || !id) return null;
  for (const group of groups) {
    if (group.id === id) return group.names[i18n.lang] || group.names.uk;
    const child = (group.children || []).find((item) => item.id === id);
    if (child) return child.names[i18n.lang] || child.names.uk;
  }
  return null;
}

function smallButton(label, onClick) {
  const button = document.createElement("button");
  button.className = "tap rounded-xl bg-bg px-3 py-2";
  button.textContent = label;
  button.onclick = onClick;
  return button;
}

async function openHistory(filter) {
  state.openFilter = filter;
  $("#history-title").textContent = filter.title;
  const list = $("#history-list");
  list.innerHTML = "";
  showTab("history");

  const items = await api.history(filter.id);
  if (items.length === 0) {
    list.innerHTML = `<p class="text-muted">${i18n.t("history.empty")}</p>`;
    return;
  }

  items.forEach((item) => {
    const card = document.createElement("a");
    card.href = item.url;
    card.target = "_blank";
    card.rel = "noopener";
    card.className = "tap flex gap-3 rounded-2xl bg-card p-3";
    card.innerHTML = `
      ${
        item.image_url
          ? `<img src="${item.image_url}" alt="" class="h-16 w-16 shrink-0 rounded-xl object-cover" />`
          : ""
      }
      <div class="min-w-0">
        <div class="truncate font-medium">${item.title}</div>
        <div class="text-sm">${formatPrice(item)}</div>
        <div class="mt-1 text-xs text-muted">
          ${i18n.t("history.published")}: ${formatDateTime(item.published_at)} ·
          ${i18n.t("history.found")}: ${formatDateTime(item.found_at)}
        </div>
      </div>`;
    list.append(card);
  });
}

// ---------- Админка (только у владельца) ----------

const ADMIN_METRICS = [
  "users",
  "activeUsers",
  "filters",
  "activeFilters",
  "listings",
  "deliveries",
  "sentLastDay",
  "payingUsers",
];

const SNAKE = {
  users: "users",
  activeUsers: "active_users",
  filters: "filters",
  activeFilters: "active_filters",
  listings: "listings",
  deliveries: "deliveries",
  sentLastDay: "sent_last_day",
  payingUsers: "paying_users",
};

async function renderAdmin() {
  const [overview, users] = await Promise.all([api.adminOverview(), api.adminUsers()]);

  const metrics = $("#admin-overview");
  metrics.innerHTML = "";
  ADMIN_METRICS.forEach((key) => {
    const tile = document.createElement("div");
    tile.className = "rounded-2xl bg-card p-4";
    tile.innerHTML = `
      <div class="text-2xl font-semibold">${overview[SNAKE[key]]}</div>
      <div class="text-xs text-muted">${i18n.t(`admin.metric.${key}`)}</div>`;
    metrics.append(tile);
  });

  const list = $("#admin-users");
  list.innerHTML = "";
  users.forEach((user) => {
    const card = document.createElement("div");
    card.className = "rounded-2xl bg-card p-4";
    const name = user.username ? `@${user.username}` : user.full_name;
    const until = user.access_until
      ? `${i18n.t("access.until")} ${formatDateTime(user.access_until)}`
      : i18n.t("admin.noAccess");
    card.innerHTML = `
      <div class="flex items-baseline justify-between gap-2">
        <div class="truncate font-medium">${name}</div>
        <div class="shrink-0 text-xs text-muted">id ${user.id}</div>
      </div>
      <div class="mt-1 text-sm text-muted">
        ${i18n.t("nav.filters")}: ${user.filters} · ${until}
      </div>`;

    const grant = document.createElement("button");
    grant.className = "tap mt-3 w-full rounded-xl bg-bg px-3 py-2 text-sm";
    grant.textContent = i18n.t("admin.grant");
    grant.onclick = async () => {
      const result = await api.adminGrant(user.id, 30);
      toast(i18n.t("admin.granted", { until: formatDateTime(result.until) }));
      await renderAdmin();
    };
    card.append(grant);
    list.append(card);
  });
}

// ---------- Избранное ----------

function listingCard(item, action) {
  const card = document.createElement("div");
  card.className = "rounded-2xl bg-card p-3";
  const price = formatPrice(item);
  const was =
    item.previous_price && item.price && Number(item.previous_price) > Number(item.price)
      ? `<s class="text-muted">${Number(item.previous_price).toLocaleString(i18n.lang)}</s> `
      : "";
  card.innerHTML = `
    <a href="${item.url}" target="_blank" rel="noopener" class="flex gap-3">
      ${
        item.image_url
          ? `<img src="${item.image_url}" alt="" class="h-16 w-16 shrink-0 rounded-xl object-cover" />`
          : ""
      }
      <div class="min-w-0">
        <div class="truncate font-medium">${item.title}</div>
        <div class="text-sm">${was}${price}</div>
        <div class="mt-1 text-xs text-muted">
          ${item.location || ""} ${item.published_at ? "· " + formatDateTime(item.published_at) : ""}
        </div>
      </div>
    </a>`;
  if (action) card.append(action);
  return card;
}

async function renderFavorites() {
  const list = $("#favorites-list");
  state.favorites = await api.favorites();
  list.innerHTML = "";
  if (state.favorites.length === 0) {
    list.innerHTML = `<p class="text-muted">${i18n.t("favorites.empty")}</p>`;
    return;
  }
  state.favorites.forEach((item) => {
    const remove = smallButton(i18n.t("favorites.remove"), async () => {
      await api.removeFavorite(item.id);
      await renderFavorites();
    });
    remove.classList.add("mt-2");
    list.append(listingCard(item, remove));
  });
}

// ---------- Запуск ----------

async function refreshMe() {
  state.me = await api.me();
  i18n.use(state.me.language_code || tg?.initDataUnsafe?.user?.language_code || "uk");
  i18n.apply();
}

async function openMain() {
  if (!state.catalog) {
    try {
      state.catalog = await api.catalog();
    } catch (error) {
      state.catalog = null; // без справочника форма всё равно работает, только без списков
    }
  }
  renderCatalogSelects();

  const isAdmin = state.me.access.is_admin;
  // Вкладка админки существует только у владельца: у остальных её нет в разметке.
  document.querySelector('[data-tab="admin"]').classList.toggle("hidden", !isAdmin);
  $("#tabbar").classList.toggle("grid-cols-4", !isAdmin);
  $("#tabbar").classList.toggle("grid-cols-5", isAdmin);

  renderAccess();
  renderTariffs();
  await Promise.all([loadFilters(), refreshStatus(), renderFavorites()]);
  if (isAdmin) await renderAdmin();
  showTab(state.tab);
  showScreen("main");
}

async function start() {
  tg?.ready();
  tg?.expand();

  try {
    await refreshMe();
  } catch (error) {
    document.body.innerHTML =
      `<div class="p-8 text-center text-muted">${error.message || "API unavailable"}</div>`;
    return;
  }

  document.querySelectorAll("nav [data-tab]").forEach((button) => {
    button.onclick = async () => {
      haptic();
      showTab(button.dataset.tab);
      // Статус и избранное живут своей жизнью — обновляем при открытии вкладки.
      if (button.dataset.tab === "subscription") await refreshStatus();
      if (button.dataset.tab === "favorites") await renderFavorites();
    };
  });
  $("#history-back").onclick = () => showTab("filters");
  bindSearchForm();

  if (state.me.needs_onboarding) {
    renderLanguageScreen();
    showScreen("language");
    return;
  }
  await openMain();
}

start();
