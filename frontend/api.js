/**
 * Клиент Web API.
 *
 * Аутентификация — подписанная Telegram строка initData: она уходит в заголовке
 * с каждым запросом, а проверяет её сервер. Свой user id фронтенд не отправляет
 * никогда: ему всё равно нельзя верить.
 */
const api = {
  base: "/api",
  initData: (window.Telegram?.WebApp?.initData || "").trim(),

  async request(path, { method = "GET", body } = {}) {
    const response = await fetch(this.base + path, {
      method,
      headers: {
        Authorization: `tma ${this.initData}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });

    if (response.status === 204) return null;

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.detail || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  },

  me: () => api.request("/me"),
  setLanguage: (code) => api.request("/me/language", { method: "PUT", body: { language_code: code } }),
  startTrial: () => api.request("/trial", { method: "POST" }),

  filters: () => api.request("/filters"),
  createFilter: (payload) => api.request("/filters", { method: "POST", body: payload }),
  toggleFilter: (id) => api.request(`/filters/${id}/toggle`, { method: "POST" }),
  deleteFilter: (id) => api.request(`/filters/${id}`, { method: "DELETE" }),
  history: (id) => api.request(`/filters/${id}/items`),

  starsInvoice: (tariff) => api.request("/payments/stars", { method: "POST", body: { tariff } }),
  cryptoInvoice: (tariff) => api.request("/payments/cryptobot", { method: "POST", body: { tariff } }),
};
