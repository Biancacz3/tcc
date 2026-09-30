"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const PAGE_SIZE = 12;
  let key = "", generation = 0, offset = 0, busy = false, hasNext = false;
  let devices = new Map(), records = [], imageUrls = new Map();
  const requests = new Set();
  const dateTime = new Intl.DateTimeFormat("pt-BR", {dateStyle: "short", timeStyle: "medium"});
  const clock = new Intl.DateTimeFormat("pt-BR", {timeStyle: "medium"});
  const day = new Intl.DateTimeFormat("pt-BR", {dateStyle: "long"});
  const format = (value, formatter = dateTime) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Data indisponível" : formatter.format(date);
  };
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  class ApiError extends Error {
    constructor(status) {
      super(status === 401 || status === 403 ? "Chave ausente, inválida ou sem permissão. Entre com a chave administrativa." :
        status === 503 ? "O serviço está temporariamente indisponível. Confira a API, o banco e o armazenamento." :
        `Não foi possível concluir a solicitação (HTTP ${status}).`);
      this.status = status;
    }
  }
  async function api(path, type = "json") {
    const controller = new AbortController();
    requests.add(controller);
    const timeout = setTimeout(() => controller.abort(), type === "blob" ? 30000 : 15000);
    try {
      const response = await fetch(`/api/v1${path}`, {
        headers: {Authorization: `Bearer ${key}`},
        cache: "no-store", credentials: "omit", redirect: "error", signal: controller.signal
      });
      if (!response.ok) throw new ApiError(response.status);
      if (type === "blob") {
        if (!(response.headers.get("content-type") || "").startsWith("image/jpeg")) {
          throw new Error("O servidor não retornou uma imagem JPEG.");
        }
        return await response.blob();
      }
      return await response.json();
    } finally {
      clearTimeout(timeout);
      requests.delete(controller);
    }
  }
  function message(error) {
    if (error instanceof ApiError) return error.message;
    return "Não foi possível comunicar com o sistema. Confira se o Docker e a API estão ligados e tente atualizar.";
  }
  function endSession(reason = "") {
    generation++;
    key = ""; busy = false; offset = 0; hasNext = false; records = []; devices.clear();
    for (const request of requests) request.abort();
    requests.clear();
    if ($("image-dialog").open) $("image-dialog").close();
    $("detail-image").removeAttribute("src");
    for (const url of imageUrls.values()) URL.revokeObjectURL(url);
    imageUrls.clear();
    $("gallery").replaceChildren();
    $("dashboard").hidden = true;
    $("login-view").hidden = false;
    $("login-error").textContent = reason;
    $("admin-key").value = "";
    $("login-button").disabled = false;
    $("login-button").textContent = "Entrar no painel →";
    $("admin-key").focus();
  }
  function controls() {
    $("refresh").disabled = busy;
    $("previous").disabled = busy || offset === 0;
    $("next").disabled = busy || !hasNext;
    $("gallery").setAttribute("aria-busy", String(busy));
  }
  function deviceName(id) { return devices.get(id) || `Câmera ${id.slice(0, 8)}`; }
  function showDetails(record) {
    const url = imageUrls.get(record.id);
    if (!url) return;
    $("detail-title").textContent = deviceName(record.device_id);
    $("detail-image").src = url;
    $("detail-image").alt = `Captura de ${deviceName(record.device_id)}, ${format(record.captured_at)}`;
    const fields = [
      ["Capturada em", format(record.captured_at)], ["Recebida em", format(record.received_at)],
      ["Resolução", `${record.width} × ${record.height} pixels`],
      ["Tamanho", `${(record.size_bytes / 1024 / 1024).toLocaleString("pt-BR", {maximumFractionDigits: 2})} MiB`],
      ["Dispositivo", record.device_id], ["ID da captura", record.id]
    ];
    $("detail-fields").replaceChildren(...fields.flatMap(([label, value]) => [element("dt", "", label), element("dd", "", value)]));
    $("image-dialog").showModal();
  }
  function card(record) {
    const button = element("button", "capture-card");
    button.type = "button";
    button.setAttribute("aria-label", `Ampliar foto de ${deviceName(record.device_id)} em ${format(record.captured_at)}`);
    button.disabled = true;
    const photo = element("div", "photo-wrap");
    const placeholder = element("span", "photo-placeholder", "Carregando imagem…");
    photo.append(placeholder);
    const content = element("div", "card-content");
    const heading = element("div", "card-heading");
    const name = element("span", "card-title", deviceName(record.device_id));
    name.title = deviceName(record.device_id);
    heading.append(name, element("span", "received-badge", "Recebida"));
    const footer = element("div", "card-footer");
    footer.append(element("span", "", `${record.width} × ${record.height}`), element("span", "", "Ampliar ↗"));
    content.append(heading, element("time", "card-date", format(record.captured_at)), footer);
    button.append(photo, content);
    button.addEventListener("click", () => showDetails(record));
    return {record, button, photo, placeholder};
  }
  function putImage(item, url) {
    const img = element("img");
    img.alt = `Foto de ${deviceName(item.record.device_id)}`;
    img.decoding = "async";
    img.onload = () => { item.placeholder.hidden = true; item.button.disabled = false; };
    img.onerror = () => { img.remove(); item.placeholder.hidden = false; item.placeholder.textContent = "Não foi possível abrir a imagem. Atualize para tentar novamente."; };
    img.src = url;
    item.photo.append(img);
  }
  async function loadImages(items, run) {
    const queue = [...items];
    async function worker() {
      while (queue.length && run === generation && key) {
        const item = queue.shift();
        const cached = imageUrls.get(item.record.id);
        if (cached) { putImage(item, cached); continue; }
        try {
          const blob = await api(`/captures/${encodeURIComponent(item.record.id)}/image`, "blob");
          if (run !== generation) return;
          const url = URL.createObjectURL(blob);
          imageUrls.set(item.record.id, url);
          putImage(item, url);
        } catch (error) {
          if (run !== generation) return;
          if (error.status === 401 || error.status === 403) { endSession(error.message); return; }
          item.placeholder.textContent = "Imagem indisponível. Clique em Atualizar para tentar novamente.";
        }
      }
    }
    await Promise.all([worker(), worker(), worker()]);
  }
  async function loadPage(target = offset) {
    if (busy || !key) return false;
    busy = true; controls();
    const run = generation;
    try {
      const [captures, cameraList] = await Promise.all([
        api(`/captures?limit=${PAGE_SIZE + 1}&offset=${target}`),
        api("/devices?limit=100&offset=0").catch(error => {
          if (error.status === 401 || error.status === 403) throw error;
          return null;
        })
      ]);
      if (run !== generation) return false;
      if (!Array.isArray(captures)) throw new Error("Formato de resposta inválido");
      if (Array.isArray(cameraList)) devices = new Map(cameraList.map(device => [device.id, device.name]));
      offset = target;
      hasNext = captures.length > PAGE_SIZE;
      records = captures.slice(0, PAGE_SIZE);
      const ids = new Set(records.map(record => record.id));
      for (const [id, url] of imageUrls) {
        if (!ids.has(id)) { URL.revokeObjectURL(url); imageUrls.delete(id); }
      }
      $("login-view").hidden = true; $("dashboard").hidden = false;
      $("stat-count").textContent = String(records.length);
      $("stat-devices").textContent = String(new Set(records.map(record => record.device_id)).size);
      $("stat-received").textContent = records.length ? format(records[0].received_at, clock) : "—";
      $("stat-received-date").textContent = records.length ? format(records[0].received_at, day) : "Nenhuma captura nesta página";
      $("page-number").textContent = `Página ${Math.floor(offset / PAGE_SIZE) + 1}`;
      $("empty-state").hidden = records.length > 0;
      $("page-error").hidden = cameraList !== null;
      $("page-error").textContent = cameraList === null ? "As fotos foram carregadas, mas não foi possível atualizar os nomes dos dispositivos." : "";
      $("updated").textContent = `Atualizado às ${clock.format(new Date())}${offset > 0 ? " · Atualização automática somente na primeira página" : ""}`;
      const items = records.map(card);
      $("gallery").replaceChildren(...items.map(item => item.button));
      await loadImages(items, run);
      return run === generation;
    } catch (error) {
      if (run !== generation) return false;
      if (error.status === 401 || error.status === 403) endSession(error.message);
      else if ($("dashboard").hidden) endSession(message(error));
      else { $("page-error").hidden = false; $("page-error").textContent = message(error) + " Os registros exibidos podem estar desatualizados."; }
      return false;
    } finally {
      if (run === generation) { busy = false; controls(); }
    }
  }
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const candidate = $("admin-key").value.trim();
    if (!candidate) return;
    key = candidate; generation++;
    $("admin-key").value = "";
    $("login-error").textContent = "";
    $("login-button").disabled = true;
    $("login-button").textContent = "Conectando…";
    if (await loadPage(0)) $("refresh").focus();
  });
  $("logout").addEventListener("click", () => endSession());
  $("refresh").addEventListener("click", () => loadPage());
  $("previous").addEventListener("click", () => loadPage(Math.max(0, offset - PAGE_SIZE)));
  $("next").addEventListener("click", () => loadPage(offset + PAGE_SIZE));
  $("close-dialog").addEventListener("click", () => $("image-dialog").close());
  $("image-dialog").addEventListener("close", () => $("detail-image").removeAttribute("src"));
  setInterval(() => {
    if (key && !busy && offset === 0 && $("auto-refresh").checked && !document.hidden && !$("image-dialog").open) loadPage(0);
  }, 30000);
  window.addEventListener("pagehide", () => endSession());
})();
