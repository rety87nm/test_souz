const state = { result: null, mode: "form" };

const form = document.getElementById("product-form");
const specsRows = document.getElementById("specs-rows");
const jsonEditor = document.getElementById("json-editor");
const jsonInput = document.getElementById("json-input");
const errorBox = document.getElementById("error-box");
const submitBtn = document.getElementById("submit-btn");
const jsonSubmitBtn = document.getElementById("json-submit");
const resultEl = document.getElementById("result");
const emptyState = document.getElementById("empty-state");
const resultActions = document.getElementById("result-actions");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addSpecRow(key = "", value = "") {
  const row = el("div", "spec-row");
  const keyInput = el("input");
  keyInput.type = "text";
  keyInput.placeholder = "Характеристика";
  keyInput.value = key;
  keyInput.setAttribute("aria-label", "Название характеристики");

  const valueInput = el("input");
  valueInput.type = "text";
  valueInput.placeholder = "Значение";
  valueInput.value = value;
  valueInput.setAttribute("aria-label", "Значение характеристики");

  const remove = el("button", "remove-spec", "×");
  remove.type = "button";
  remove.title = "Удалить характеристику";
  remove.setAttribute("aria-label", "Удалить характеристику");
  remove.addEventListener("click", () => row.remove());

  row.append(keyInput, valueInput, remove);
  specsRows.append(row);
}

function collectForm() {
  const specs = {};
  specsRows.querySelectorAll(".spec-row").forEach((row) => {
    const inputs = row.querySelectorAll("input");
    const key = inputs[0].value.trim();
    const value = inputs[1].value.trim();
    if (key) specs[key] = value;
  });

  return {
    name: document.getElementById("name").value.trim(),
    manufacturer: document.getElementById("manufacturer").value.trim(),
    specs,
    description: document.getElementById("description").value.trim(),
  };
}

function fillForm(product) {
  document.getElementById("name").value = product.name || "";
  document.getElementById("manufacturer").value = product.manufacturer || "";
  document.getElementById("description").value = product.description || "";
  specsRows.innerHTML = "";
  const specs = product.specs || {};
  Object.entries(specs).forEach(([key, value]) => addSpecRow(key, value));
  if (!Object.keys(specs).length) addSpecRow();
  syncJsonFromForm();
}

function syncJsonFromForm() {
  jsonInput.value = JSON.stringify(collectForm(), null, 2);
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
  form.classList.toggle("hidden", mode !== "form");
  jsonEditor.classList.toggle("hidden", mode !== "json");
  hideError();
  if (mode === "json") {
    syncJsonFromForm();
  }
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function hideError() {
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
}

function setLoading(isLoading) {
  submitBtn.disabled = isLoading;
  jsonSubmitBtn.disabled = isLoading;
  submitBtn.classList.toggle("loading", isLoading);
  submitBtn.querySelector(".btn-text").textContent = isLoading
    ? "Генерирую карточку…"
    : "Сформировать карточку";
}

function extractError(data) {
  if (typeof data === "string") return data;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail
      .map((item) => `${item.field ? item.field + ": " : ""}${item.msg || item.message || ""}`)
      .join("\n");
  }
  if (data.error) {
    const details = Array.isArray(data.details)
      ? data.details.map((item) => `${item.field ? item.field + ": " : ""}${item.message || ""}`)
      : [];
    return [data.error, ...details].join("\n");
  }
  return "Не удалось выполнить запрос";
}

async function generate(payload) {
  hideError();
  if (!payload.name) {
    showError("Укажите название товара.");
    return;
  }
  const hasSpecs = payload.specs && Object.keys(payload.specs).length > 0;
  const hasDescription = (payload.description || "").trim().length > 0;
  if (!hasSpecs && !hasDescription) {
    showError("Заполните хотя бы одну характеристику или описание.");
    return;
  }

  setLoading(true);
  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(`Сервер вернул ответ без JSON (HTTP ${response.status}).`);
    }

    if (!response.ok) throw new Error(extractError(data));

    state.result = data;
    renderResult(data);
  } catch (error) {
    showError(error.message || "Неизвестная ошибка");
  } finally {
    setLoading(false);
  }
}

function renderResult(data) {
  const card = data.card;

  document.getElementById("result-title").textContent = card.seo_title;
  const meta = data.meta || {};
  const seconds = typeof meta.duration_ms === "number" ? (meta.duration_ms / 1000).toFixed(1) : "—";
  document.getElementById("result-meta").textContent =
    `Модель: ${meta.model || "—"} · Попыток: ${meta.attempts || 1} · Время: ${seconds} с`;

  document.getElementById("result-description").textContent = card.seo_description;

  const specsBody = document.getElementById("result-specs");
  specsBody.innerHTML = "";
  (card.key_specs || []).forEach((item) => {
    const tr = el("tr");
    tr.append(el("td", "", item.name));
    const valueCell = el("td", "", item.value);
    if (/^данных нет$/i.test(item.value.trim())) valueCell.classList.add("no-data");
    tr.append(valueCell);
    specsBody.append(tr);
  });

  const queries = document.getElementById("result-queries");
  queries.innerHTML = "";
  (card.search_queries || []).forEach((query) => queries.append(el("span", "chip", query)));

  document.getElementById("result-commercial").textContent = card.commercial_block;

  const missing = document.getElementById("result-missing");
  missing.innerHTML = "";
  if ((card.missing_fields || []).length) {
    card.missing_fields.forEach((field) => missing.append(el("li", "", field)));
  } else {
    missing.append(el("li", "", "Все необходимые данные заполнены."));
  }

  const warningsSection = document.getElementById("warnings-section");
  const warnings = document.getElementById("result-warnings");
  warnings.innerHTML = "";
  if ((card.warnings || []).length) {
    card.warnings.forEach((warning) => warnings.append(el("li", "", warning)));
    warningsSection.classList.remove("hidden");
  } else {
    warningsSection.classList.add("hidden");
  }

  emptyState.classList.add("hidden");
  resultEl.classList.remove("hidden");
  resultActions.classList.remove("hidden");

  if (window.matchMedia("(max-width: 980px)").matches) {
    resultEl.closest(".result-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  const previous = button.textContent;
  button.textContent = "Скопировано";
  setTimeout(() => { button.textContent = previous; }, 1400);
}

function initExamples() {
  const container = document.getElementById("examples");
  if (!EXAMPLES.length) return;
  EXAMPLES.forEach((product, index) => {
    const button = el("button", "example-btn", `Пример ${index + 1}: ${product.name}`);
    button.type = "button";
    button.addEventListener("click", () => {
      fillForm(product);
      setMode("form");
      hideError();
    });
    container.append(button);
  });
}

document.getElementById("add-spec").addEventListener("click", () => {
  addSpecRow();
  const rows = specsRows.querySelectorAll(".spec-row");
  const last = rows[rows.length - 1];
  if (last) last.querySelector("input").focus();
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  generate(collectForm());
});

jsonSubmitBtn.addEventListener("click", () => {
  let payload;
  try {
    payload = JSON.parse(jsonInput.value);
  } catch (error) {
    showError("Некорректный JSON: " + error.message);
    return;
  }
  generate(payload);
});

document.querySelectorAll(".mode-btn").forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.mode));
});

document.getElementById("copy-md").addEventListener("click", (event) => {
  if (state.result) copyText(state.result.markdown, event.currentTarget);
});

document.getElementById("copy-json").addEventListener("click", (event) => {
  if (state.result) copyText(JSON.stringify(state.result.card, null, 2), event.currentTarget);
});

async function checkHealth() {
  const box = document.getElementById("health");
  const text = document.getElementById("health-text");
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "LLM недоступна");
    box.classList.add("ok");
    text.textContent = `LLM: ${data.model}`;
  } catch {
    box.classList.add("bad");
    text.textContent = "LLM недоступна";
  }
}

initExamples();
addSpecRow();
setMode("form");
checkHealth();
