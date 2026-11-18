"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const PAGE_SIZE = 12, HISTORY_SIZE = 20;
  let session = null, generation = 0, view = "captures", offset = 0, busy = false, hasNext = false;
  let positiveOffset = 0, positiveBusy = false, positiveTotal = 0;
  let positiveFilter = {plant_id: "", from_date: "", to_date: ""};
  let devices = new Map(), records = [], imageUrls = new Map();
  let detailGeneration = 0, selectedRecord = null, selectedAnalysis = null, history = [], selectedJob = null;
  let detailTimer = null, detailBusy = false, historyPinned = null;
  let captureFilter = {analysis_state: "", plant_id: "", from_date: "", to_date: ""};
  let usersOffset = 0, usersTotal = 0, usersBusy = false, editUser = null, resetUser = null;
  let uploadBusy = false, uploadIdentity = null, userMutationBusy = false, resetMutationBusy = false, pendingPage = null, pendingPositive = null;
  const VIEWS = ["captures", "positive", "devices", "users", "account"];
  const requests = new Set();
  const dateTime = new Intl.DateTimeFormat("pt-BR", {dateStyle: "short", timeStyle: "medium"});
  const clock = new Intl.DateTimeFormat("pt-BR", {timeStyle: "medium"});
  const day = new Intl.DateTimeFormat("pt-BR", {dateStyle: "long"});
  const format = (value, formatter = dateTime) => {
    if (!value) return "Data indisponível";
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
    constructor(status, detail) {
      super(status === 401 ? "E-mail ou senha incorretos, ou sessão expirada. Entre novamente." :
        status === 403 ? "Seu usuário não tem permissão para esta ação." :
        status === 429 ? "Muitas tentativas. Aguarde um pouco e tente novamente." :
        status === 503 ? "O serviço está temporariamente indisponível. Tente novamente em instantes." :
        `Não foi possível concluir a solicitação (HTTP ${status}).`);
      this.status = status;
      if (typeof detail === "string" && ![401,403,429].includes(status)) this.message = detail;
    }
  }
  function connection(ok) {
    $("connection-status").textContent = ok ? "Conectado" : "Reconectando…";
    $("connection-status").classList.toggle("connection-offline", !ok);
  }
  async function api(path, type = "json", method = "GET", body, extraHeaders = {}) {
    const controller = new AbortController();
    requests.add(controller);
    const raw = extraHeaders["Content-Type"] === "image/jpeg";
    const timeout = setTimeout(() => controller.abort(), raw ? 60000 : type === "blob" ? 30000 : 15000);
    const headers = {...extraHeaders};
    if (body !== undefined && !raw) headers["Content-Type"] = "application/json";
    if (!["GET", "HEAD"].includes(method) && session?.csrf_token) headers["X-CSRF-Token"] = session.csrf_token;
    try {
      const response = await fetch(`/api/v1${path}`, {
        method, headers, body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
        cache: "no-store", credentials: "same-origin", redirect: "error", signal: controller.signal
      });
      connection(true);
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new ApiError(response.status, data.detail);
      }
      if (response.status === 204) return null;
      if (type === "blob") {
        if (!(response.headers.get("content-type") || "").startsWith("image/jpeg")) throw new Error("Resposta não é JPEG");
        return await response.blob();
      }
      return await response.json();
    } catch (error) { if (!(error instanceof ApiError)) connection(false); throw error; }
    finally { clearTimeout(timeout); requests.delete(controller); }
  }
  function message(error) {
    return error instanceof ApiError ? error.message : "Não foi possível comunicar com o sistema. Confira a conexão e tente atualizar.";
  }
  function authFailure(error) {
    if (error.status !== 401) return false;
    endSession("Sua sessão expirou. Entre novamente."); return true;
  }
  function endSession(reason = "") {
    resetAnalysis(); generation++; session = null; busy = false; positiveBusy = false;
    offset = 0; positiveOffset = 0; hasNext = false; records = []; devices.clear();
    for (const request of requests) request.abort();
    requests.clear();
    for (const id of ["image-dialog", "upload-dialog", "user-dialog", "reset-dialog"]) if ($(id).open) $(id).close();
    for (const id of ["current-password", "new-password", "confirm-password", "user-password", "reset-password"]) $(id).value = "";
    $("users-body").replaceChildren(); $("devices-list").replaceChildren(); usersBusy = false; uploadBusy = false;
    uploadIdentity = null; historyPinned = null; pendingPage = null; pendingPositive = null; $("upload-form").reset();
    $("detail-image").removeAttribute("src");
    for (const url of imageUrls.values()) URL.revokeObjectURL(url);
    imageUrls.clear(); $("gallery").replaceChildren(); $("positive-body").replaceChildren();
    $("dashboard").hidden = true; $("login-view").hidden = false;
    $("login-error").textContent = reason; $("login-password").value = "";
    $("login-button").disabled = false; $("login-button").textContent = "Entrar no painel →";
  }
  function showSession() {
    $("login-view").hidden = true; $("dashboard").hidden = false;
    $("nav-users").hidden = session.user.role !== "admin";
    if (session.user.role !== "admin") $("users-body").replaceChildren();
    $("open-upload").hidden = session.user.role !== "admin";
    $("account-email").textContent = session.user.email;
    $("session-name").textContent = `${session.user.name || session.user.email} · ${session.user.role === "admin" ? "Administrador" : "Consulta"}`;
  }
  function controls() {
    $("refresh").disabled = busy; $("previous").disabled = busy || offset === 0;
    $("next").disabled = busy || !hasNext; $("gallery").setAttribute("aria-busy", String(busy));
    $("positive-refresh").disabled = positiveBusy;
    $("positive-previous").disabled = positiveBusy || positiveOffset === 0;
    $("positive-next").disabled = positiveBusy || positiveOffset + HISTORY_SIZE >= positiveTotal;
    $("positive-body").setAttribute("aria-busy", String(positiveBusy));
  }
  function analysisState(item) {
    return (item?.analysis_state && item.analysis_state !== "legacy" ? item.analysis_state : null) || (item?.detection_count > 0 ? "positive" : item?.detection_count === 0 ? "no_detections" : null);
  }
  function resultLabel(state) {
    return ({positive: "Praga detectada", review: "Região não identificada", no_detections: "Nenhuma detecção", inconclusive: "Imagem inconclusiva"})[state] || "Análise concluída";
  }
  function badgeClass(state) {
    return ({positive: "positive-badge", review: "review-badge", inconclusive: "review-badge", no_detections: "clear-badge"})[state] || "";
  }
  function deviceName(id) { return devices.get(id) || (id ? `Câmera ${String(id).slice(0,8)}` : "Câmera não informada"); }
  function plantName(record) { return record.plant_name || record.plant_id || "Planta não informada"; }
  function jobLabel(status, count) {
    if (status === "completed") return count > 0 ? "Praga detectada" : count === 0 ? "Nenhuma detecção" : "Análise concluída";
    return ({queued:"Na fila de análise",processing:"Analisando",retry:"Nova tentativa agendada",failed:"Falha na análise",not_scheduled:"Sem análise agendada"})[status] || "Recebida";
  }
  async function imageFor(record, run = generation) {
    if (imageUrls.has(record.id)) return imageUrls.get(record.id);
    const blob = await api(`/captures/${encodeURIComponent(record.id)}/image`, "blob");
    if (run !== generation || !session) return null;
    const url = URL.createObjectURL(blob); imageUrls.set(record.id, url);
    while (imageUrls.size > 36) {
      const oldest = [...imageUrls.keys()].find(id => id !== selectedRecord?.id && id !== record.id);
      if (!oldest) break;
      URL.revokeObjectURL(imageUrls.get(oldest)); imageUrls.delete(oldest);
    }
    return url;
  }
  async function showDetails(record, preferredAnalysisId = null) {
    resetAnalysis(); selectedRecord = record; historyPinned = preferredAnalysisId;
    const run = detailGeneration;
    $("detail-title").textContent = plantName(record);
    $("detail-image").removeAttribute("src");
    $("detail-image").alt = `Captura de ${plantName(record)}, ${format(record.captured_at)}`;
    const fields = [["Planta", plantName(record)], ["Câmera", deviceName(record.device_id)],
      ["Capturada em", format(record.captured_at)], ["Recebida em", format(record.received_at)],
      ["Resolução", `${record.width} × ${record.height} pixels`],
      ["Tamanho", `${(record.size_bytes/1024/1024).toLocaleString("pt-BR",{maximumFractionDigits:2})} MiB`]];
    if (record.marker_id != null) fields.push(["Marcador", String(record.marker_id)]);
    if (record.plant_side) fields.push(["Lado fotografado", record.plant_side]);
    fields.push(["ID da captura", record.id]);
    $("detail-fields").replaceChildren(...fields.flatMap(([label,value]) => [element("dt","",label),element("dd","",value)]));
    if (!$("image-dialog").open) $("image-dialog").showModal();
    void loadAnalyses(record, run, preferredAnalysisId);
    try {
      const url = await imageFor(record);
      if (run === detailGeneration && url) $("detail-image").src = url;
    } catch (error) {
      if (run === detailGeneration && !authFailure(error)) $("analysis-error").textContent = "Não foi possível carregar a fotografia. Feche e abra novamente.";
    }
  }
  function resetAnalysis() {
    detailGeneration++; clearTimeout(detailTimer); detailTimer = null; detailBusy = false;
    selectedRecord = null; selectedAnalysis = null; selectedJob = null; history = []; historyPinned = null;
    $("analysis-canvas").hidden = true; $("analysis-canvas").width = 1; $("analysis-canvas").height = 1;
    $("detail-image").hidden = false; $("analysis-message").textContent = "Consultando análise…";
    $("analysis-error").textContent = ""; $("analysis-meta").textContent = "";
    $("analysis-detections").replaceChildren(); $("analysis-history").replaceChildren(); $("history-label").hidden = true;
    $("analyze-button").hidden = true; $("analyze-button").disabled = true;
    $("show-boxes").checked = true; $("show-boxes").disabled = true;
    $("show-candidates").checked = true; $("show-candidates").disabled = true;
    $("reanalyze-button").hidden = true; $("analysis-legend").hidden = true;
    $("analysis-progress").textContent = ""; $("analysis-warnings").textContent = "";
  }
  function drawAnalysis() {
    const img = $("detail-image"), canvas = $("analysis-canvas");
    if (!selectedAnalysis || !$("show-boxes").checked || !img.complete || !img.naturalWidth) {
      canvas.hidden = true; img.hidden = false; return;
    }
    if (selectedAnalysis.width !== img.naturalWidth || selectedAnalysis.height !== img.naturalHeight) {
      $("analysis-error").textContent = "As dimensões do resultado não correspondem à fotografia.";
      canvas.hidden = true; img.hidden = false; return;
    }
    canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d"); ctx.drawImage(img,0,0);
    const fontSize = Math.max(14,Math.round(canvas.width/65));
    ctx.font = `600 ${fontSize}px sans-serif`; ctx.lineWidth = Math.max(2,canvas.width/350);
    const marks = (selectedAnalysis.detections || []).map(d => ({...d, positive: true}));
    if ($("show-candidates").checked) marks.push(...(selectedAnalysis.candidates || []).filter(c => c.status === "unidentified").map(c => ({...c, positive: false})));
    // Draw unknown regions first so verified model marks remain visible.
    marks.sort((a,b) => Number(a.positive)-Number(b.positive));
    for (const detection of marks) {
      if (!Array.isArray(detection.xyxy) || detection.xyxy.length !== 4 || !detection.xyxy.every(Number.isFinite)) continue;
      const [x1,y1,x2,y2] = detection.xyxy;
      if (x1 < 0 || y1 < 0 || x2 <= x1 || y2 <= y1 || x2 > canvas.width || y2 > canvas.height) continue;
      const label = detection.positive ? "Praga detectada" : "Região não identificada";
      ctx.strokeStyle = detection.positive ? "#b8ee69" : "#ffc452";
      ctx.strokeRect(x1,y1,x2-x1,y2-y1);
      const tw = Math.min(canvas.width,ctx.measureText(label).width+12);
      const tx = Math.max(0,Math.min(x1,canvas.width-tw)), ty = Math.max(0,y1-fontSize-9);
      ctx.fillStyle = detection.positive ? "#193e30" : "#654609"; ctx.fillRect(tx,ty,tw,fontSize+9);
      ctx.fillStyle = "#fff"; ctx.fillText(label,tx+6,ty+fontSize+1);
    }
    canvas.hidden = false; img.hidden = true;
  }
  function renderJob() {
    const status = selectedJob?.status;
    const descriptions = {
      queued: "Foto salva. Aguardando análise automática.",
      processing: "A análise está em andamento. O resultado aparecerá aqui automaticamente.",
      retry: "A análise será tentada novamente automaticamente.",
      failed: "Não foi possível concluir a análise após as tentativas automáticas.",
      not_scheduled: "Esta captura ainda não tem uma análise agendada.",
      completed: "Análise concluída. Carregando resultado…"
    };
    if (!selectedAnalysis) $("analysis-message").textContent = descriptions[status] || "Aguardando informações da análise.";
    $("analysis-progress").textContent = selectedAnalysis && ["queued", "processing", "retry"].includes(status) ? "Uma nova análise está em andamento. Exibindo o resultado anterior até ela terminar." : "";
    if (["retry","failed"].includes(status)) $("analysis-error").textContent = "A análise não terminou. A foto está salva; tente novamente ou confira o serviço de análise.";
    $("reanalyze-button").hidden = !selectedAnalysis || session?.user.role !== "admin" || ["queued", "processing", "retry", "failed"].includes(status);
    $("reanalyze-button").disabled = detailBusy;
    const retry = ["failed","not_scheduled"].includes(status) && session?.user.role === "admin";
    $("analyze-button").hidden = !retry; $("analyze-button").disabled = !retry || detailBusy;
    $("analyze-button").textContent = status === "not_scheduled" ? "Agendar análise desta foto" : "Tentar análise novamente";
  }
  function renderAnalysis(result) {
    selectedAnalysis = result; $("show-boxes").disabled = !result;
    const unknown = result?.unidentified_count ?? (result?.candidates || []).filter(c => c.status === "unidentified").length;
    $("show-candidates").disabled = !unknown; $("analysis-legend").hidden = !result;
    $("analysis-detections").replaceChildren(); $("analysis-warnings").textContent = "";
    if (!result) { $("analysis-meta").textContent = ""; renderJob(); }
    else {
      const state = analysisState(result);
      $("analysis-message").textContent = ({
        positive: `Praga detectada · ${result.detection_count} marcação(ões).${unknown ? ` Há também ${unknown} região(ões) não identificada(s).` : ""}`,
        review: `${unknown} região(ões) não identificada(s). Confira os detalhes em amarelo.`,
        no_detections: "Nenhuma detecção nesta análise. Isso não confirma ausência de insetos.",
        inconclusive: "Não foi possível avaliar esta imagem com segurança. Confira o enquadramento e tente uma foto mais nítida."
      })[state] || "Análise concluída. Confira a imagem.";
      $("analysis-message").className = `result-message ${badgeClass(state)}`;
      $("analysis-meta").textContent = `Análise de ${format(result.created_at)}${result.analysis_state === "legacy" || result.method === "legacy_full_image" ? " · Versão anterior" : ""}${historyPinned ? " · Resultado do histórico" : ""}`;
      if ((result.warnings || []).length) $("analysis-warnings").textContent = "Esta análise tem ressalvas. Confira a imagem e as informações abaixo.";
      $("analysis-detections").append(element("li", "", `Modelo: ${result.model_name || "não informado"}`));
      $("analysis-detections").append(...(result.detections || []).map((d,i) =>
        element("li","",`${i+1}. Classe do modelo: ${d.label || d.class_name || result.model_scope || "não informada"}`)));
      $("analysis-detections").append(...(result.warnings || []).map(w => element("li", "", String(w))));
      renderJob();
    }
    drawAnalysis();
  }
  function renderHistory(preferredId = null) {
    const keepId = preferredId || historyPinned;
    $("analysis-history").replaceChildren(...history.map((item,index) => {
      const option = element("option","",`${format(item.created_at)} · ${resultLabel(analysisState(item))}`);
      option.value = String(index); return option;
    }));
    $("history-label").hidden = history.length < 2;
    let selected = history.findIndex(item => item.id === keepId);
    if (selected < 0) selected = 0;
    $("analysis-history").value = String(selected);
    renderAnalysis(history[selected] || null);
  }
  async function loadAnalyses(record, run, preferredId = null) {
    if (detailBusy || run !== detailGeneration || !session) return;
    detailBusy = true;
    try {
      const [saved,job] = await Promise.all([api(`/captures/${record.id}/analyses`),api(`/captures/${record.id}/analysis-job`)]);
      if (run !== detailGeneration) return;
      history = saved; selectedJob = job; $("analysis-error").textContent = "";
      renderHistory(preferredId);
    } catch (error) {
      if (run !== detailGeneration || authFailure(error)) return;
      $("analysis-error").textContent = message(error);
    } finally {
      if (run === detailGeneration) {
        detailBusy = false; renderJob(); clearTimeout(detailTimer);
        // Keep polling the open photo even after a transient request failure.
        if (session && $("image-dialog").open) detailTimer = setTimeout(() => {
          if (!document.hidden) void loadAnalyses(record,run);
          else detailTimer = setTimeout(() => loadAnalyses(record,run),3000);
        },3000);
      }
    }
  }
  $("analyze-button").addEventListener("click",async () => {
    if (!selectedRecord || detailBusy) return;
    const run = detailGeneration, record = selectedRecord;
    clearTimeout(detailTimer); detailBusy = true; $("analyze-button").disabled = true; $("analysis-error").textContent = "";
    try {
      const job = await api(`/captures/${record.id}/analysis-job/retry`,"json","POST");
      if (run === detailGeneration) { selectedJob = job; renderJob(); }
    } catch (error) {
      if (run === detailGeneration && !authFailure(error)) $("analysis-error").textContent = message(error);
    } finally {
      if (run === detailGeneration) { detailBusy = false; void loadAnalyses(record,run); }
    }
  });
  $("reanalyze-button").addEventListener("click",async () => {
    if (!selectedRecord || detailBusy) return;
    const run = detailGeneration, record = selectedRecord;
    detailBusy = true; clearTimeout(detailTimer); $("reanalyze-button").disabled = true;
    try {
      const job = await api(`/captures/${record.id}/reanalyze`, "json", "POST");
      if (run === detailGeneration) { selectedJob = job; historyPinned = null; renderJob(); }
    } catch (error) { if (run === detailGeneration && !authFailure(error)) $("analysis-error").textContent = message(error); }
    finally { if (run === detailGeneration) { detailBusy = false; void loadAnalyses(record,run); } }
  });
  $("analysis-history").addEventListener("change",() => {
    const result = history[Number($("analysis-history").value)]; historyPinned = result?.id || null; renderAnalysis(result);
  });
  $("show-candidates").addEventListener("change",drawAnalysis);
  $("show-boxes").addEventListener("change",drawAnalysis);
  $("detail-image").addEventListener("load",drawAnalysis);
  function card(record) {
    const button = element("button","capture-card"); button.type = "button";
    button.setAttribute("aria-label",`Abrir foto de ${plantName(record)} em ${format(record.captured_at)}`);
    const photo = element("div","photo-wrap"), placeholder = element("span","photo-placeholder","Carregando imagem…");
    photo.append(placeholder);
    const content = element("div","card-content"), heading = element("div","card-heading");
    const name = element("span","card-title",plantName(record)); name.title = plantName(record);
    const state = analysisState(record);
    const pending = ["queued", "processing", "retry", "failed"].includes(record.analysis_status);
    const badge = element("span",`received-badge ${pending ? record.analysis_status === "failed" ? "failed-badge" : "pending-badge" : badgeClass(state)}`, pending || !state ? jobLabel(record.analysis_status,record.detection_count) : resultLabel(state));
    heading.append(name,badge);
    const footer = element("div","card-footer"); footer.append(element("span","",record.source === "manual" ? "Enviada pelo painel" : deviceName(record.device_id)),element("span","","Abrir ↗"));
    content.append(heading,element("time","card-date",format(record.captured_at)));
    if (state === "positive" && record.unidentified_count > 0) content.append(element("p","card-review-note","Também há regiões para conferir"));
    content.append(footer); button.append(photo,content);
    button.addEventListener("click",() => showDetails(record)); return {record,button,photo,placeholder};
  }
  async function loadImages(items,run) {
    const queue = [...items];
    async function worker() {
      while (queue.length && run === generation && session) {
        const item = queue.shift();
        try {
          const url = await imageFor(item.record,run);
          if (run !== generation || !url) return;
          const img = element("img"); img.alt = `Foto de ${plantName(item.record)}`; img.decoding = "async";
          img.onload = () => { item.placeholder.hidden = true; };
          img.onerror = () => { img.remove(); item.placeholder.hidden = false; item.placeholder.textContent = "Imagem indisponível"; };
          img.src = url; item.photo.append(img);
        } catch (error) {
          if (run !== generation || authFailure(error)) return;
          item.placeholder.textContent = "Imagem indisponível. Atualize para tentar novamente.";
        }
      }
    }
    await Promise.all([worker(),worker(),worker()]);
  }
  async function loadPage(target = offset) {
    if (!session) return false;
    if (busy) { pendingPage = target; return false; }
    busy = true; controls(); const run = generation;
    try {
      const params = new URLSearchParams({limit:String(PAGE_SIZE+1),offset:String(target)});
      for (const [name,value] of Object.entries(captureFilter)) if (value) params.set(name,value);
      const [captures,cameraList,summary] = await Promise.all([
        api(`/captures?${params}`), api("/devices?limit=100&offset=0").catch(error => {
          if (error.status === 401) throw error; return null;
        }), api("/dashboard/summary").catch(error => { if (error.status === 401) throw error; return null; })
      ]);
      if (run !== generation) return false;
      if (!Array.isArray(captures)) throw new Error("Formato inválido");
      if (Array.isArray(cameraList)) devices = new Map(cameraList.map(device => [device.id,device.name]));
      offset = target; hasNext = captures.length > PAGE_SIZE; records = captures.slice(0,PAGE_SIZE);
      showSession();
      if (summary) {
        $("stat-count").textContent = String(summary.captures);
        $("stat-positive").textContent = String(summary.positive_captures);
        $("stat-review").textContent = String(summary.review_captures);
        $("stat-queued").textContent = String(summary.queued_captures);
        $("stat-failed").textContent = summary.failed_captures ? `${summary.failed_captures} foto(s) com falha na análise` : "Atualização automática";
        $("stat-devices").textContent = String(summary.devices);
        $("stat-received").textContent = summary.last_received_at ? format(summary.last_received_at,clock) : "—";
        $("stat-received-date").textContent = summary.last_received_at ? `· ${format(summary.last_received_at,day)}` : "";
      }
      $("page-number").textContent = `Página ${Math.floor(offset/PAGE_SIZE)+1}`; $("empty-state").hidden = records.length > 0;
      $("page-error").hidden = cameraList !== null && summary !== null;
      $("page-error").textContent = cameraList === null ? "Não foi possível atualizar os nomes das câmeras." : summary === null ? "O resumo não foi atualizado. As fotos continuam disponíveis; tente atualizar em instantes." : "";
      $("updated").textContent = `Atualizado às ${clock.format(new Date())}${offset > 0 ? " · Atualização automática somente na primeira página" : ""}`;
      const items = records.map(card); $("gallery").replaceChildren(...items.map(item => item.button));
      await loadImages(items,run); return run === generation;
    } catch (error) {
      if (run !== generation || authFailure(error)) return false;
      $("page-error").hidden = false; $("page-error").textContent = message(error); return false;
    } finally { if (run === generation) {
      busy = false; controls();
      if (pendingPage !== null) { const target = pendingPage; pendingPage = null; void loadPage(target); }
    } }
  }
  async function loadPositive(target = positiveOffset) {
    if (!session) return;
    if (positiveBusy) { pendingPositive = target; return; }
    positiveBusy = true; controls(); const run = generation;
    const params = new URLSearchParams({limit:String(HISTORY_SIZE),offset:String(target)});
    for (const [name,value] of Object.entries(positiveFilter)) if (value) params.set(name,value);
    try {
      const result = await api(`/plants/positive-history?${params}`);
      if (run !== generation) return;
      if (!Array.isArray(result.items)) throw new Error("Formato inválido");
      positiveOffset = target; positiveTotal = result.total;
      $("positive-body").replaceChildren(...result.items.map(item => {
        if (item.device_name) devices.set(item.device_id,item.device_name);
        const row = element("tr"), plant = element("td");
        plant.append(element("strong","",plantName(item)));
        if (item.plant_id) plant.append(element("small","",`Identificador: ${item.plant_id}`));
        else if (item.marker_id != null) plant.append(element("small","",`Marcador ${item.marker_id}`));
        plant.append(element("small","",item.device_name || deviceName(item.device_id)));
        const date = element("td","",format(item.captured_at));
        date.append(element("small","",`Análise: ${format(item.analyzed_at)}`));
        const status = element("td"); status.append(element("span","received-badge positive-badge","Praga detectada"));
        status.append(element("small","",`${item.detection_count} marcação(ões)`));
        const action = element("td"), open = element("button","secondary","Ver foto"); open.type = "button";
        open.setAttribute("aria-label",`Ver foto de ${plantName(item)} em ${format(item.captured_at)}`);
        open.addEventListener("click",async () => {
          open.disabled = true;
          try {
            const record = await api(`/captures/${encodeURIComponent(item.capture_id)}`);
            if (run === generation) await showDetails(record,item.analysis_id);
          } catch (error) {
            if (run === generation && !authFailure(error)) { $("positive-error").hidden = false; $("positive-error").textContent = message(error); }
          } finally { open.disabled = false; }
        });
        action.append(open); row.append(plant,date,status,action); return row;
      }));
      $("positive-empty").hidden = result.items.length > 0; $("positive-table-wrap").hidden = !result.items.length;
      $("positive-summary").textContent = `${result.total} registro(s) positivo(s) no período · atualizado às ${clock.format(new Date())}`;
      $("positive-page-number").textContent = `Página ${Math.floor(positiveOffset/HISTORY_SIZE)+1}`;
      $("positive-error").hidden = true;
    } catch (error) {
      if (run === generation && !authFailure(error)) { $("positive-error").hidden = false; $("positive-error").textContent = message(error); }
    } finally { if (run === generation) {
      positiveBusy = false; controls();
      if (pendingPositive !== null) { const target = pendingPositive; pendingPositive = null; void loadPositive(target); }
    } }
  }
  function switchView(next) {
    if (next === "users" && session?.user.role !== "admin") return;
    view = next;
    for (const name of VIEWS) {
      $(`${name}-view`).hidden = view !== name;
      $(`nav-${name}`).classList.toggle("nav-active",view === name);
      if (view === name) $(`nav-${name}`).setAttribute("aria-current","page"); else $(`nav-${name}`).removeAttribute("aria-current");
    }
    if (view === "positive") return loadPositive();
    else if (view === "captures") return loadPage();
    else if (view === "devices") return loadDevices();
    else if (view === "users") return loadUsers();
  }
  $("login-form").addEventListener("submit",async event => {
    event.preventDefault(); if ($("login-button").disabled) return;
    $("login-button").disabled = true; $("login-button").textContent = "Conectando…"; $("login-error").textContent = "";
    try {
      session = await api("/auth/login","json","POST",{email:$("login-email").value.trim(),password:$("login-password").value});
      generation++; $("login-password").value = ""; showSession(); switchView("captures"); $("refresh").focus();
    } catch (error) { endSession(message(error)); }
    finally { $("login-button").disabled = false; $("login-button").textContent = "Entrar no painel →"; }
  });
  $("logout").addEventListener("click",async () => {
    $("logout").disabled = true;
    try { await api("/auth/logout","json","POST"); endSession(); }
    catch (error) {
      if (error.status === 401) endSession();
      else {
        const target = view === "account" ? "password-error" : view === "captures" ? "page-error" : `${view}-error`;
        $(target).hidden = false; $(target).textContent = "Não foi possível encerrar a sessão no servidor. Confira a conexão e tente Sair novamente.";
      }
    } finally { $("logout").disabled = false; }
  });
  for (const name of VIEWS) $(`nav-${name}`).addEventListener("click",() => switchView(name));
  $("refresh").addEventListener("click",() => loadPage());
  $("previous").addEventListener("click",() => loadPage(Math.max(0,offset-PAGE_SIZE)));
  $("next").addEventListener("click",() => loadPage(offset+PAGE_SIZE));
  $("positive-refresh").addEventListener("click",() => loadPositive());
  $("positive-previous").addEventListener("click",() => loadPositive(Math.max(0,positiveOffset-HISTORY_SIZE)));
  $("positive-next").addEventListener("click",() => loadPositive(positiveOffset+HISTORY_SIZE));
  $("positive-filters").addEventListener("submit",event => {
    event.preventDefault();
    if ($("filter-from").value && $("filter-to").value && $("filter-from").value > $("filter-to").value) {
      $("positive-error").hidden = false; $("positive-error").textContent = "A data inicial deve ser anterior ou igual à final."; return;
    }
    positiveFilter = {plant_id:$("filter-plant").value.trim(),from_date:$("filter-from").value,to_date:$("filter-to").value};
    void loadPositive(0);
  });
  $("clear-filters").addEventListener("click",() => {
    $("positive-filters").reset(); positiveFilter = {plant_id:"",from_date:"",to_date:""}; void loadPositive(0);
  });
  $("capture-filters").addEventListener("submit",event => {
    event.preventDefault();
    if ($("capture-from").value && $("capture-to").value && $("capture-from").value > $("capture-to").value) {
      $("page-error").hidden = false; $("page-error").textContent = "A data inicial deve ser anterior ou igual à final."; return;
    }
    captureFilter = {analysis_state:$("capture-state").value,plant_id:$("capture-plant").value.trim(),from_date:$("capture-from").value,to_date:$("capture-to").value};
    void loadPage(0);
  });
  $("capture-clear").addEventListener("click",() => {
    $("capture-filters").reset(); captureFilter = {analysis_state:"",plant_id:"",from_date:"",to_date:""}; void loadPage(0);
  });
  $("close-dialog").addEventListener("click",() => $("image-dialog").close());
  $("image-dialog").addEventListener("close",() => { resetAnalysis(); $("detail-image").removeAttribute("src"); });
  setInterval(() => {
    if (!session || document.hidden || ["image-dialog", "upload-dialog", "user-dialog", "reset-dialog"].some(id => $(id).open)) return;
    if (view === "captures" && offset === 0 && $("auto-refresh").checked) void loadPage(0);
    if (view === "positive" && positiveOffset === 0) void loadPositive(0);
  },30000);
  window.addEventListener("pagehide",() => {
    clearTimeout(detailTimer); for (const request of requests) request.abort();
    for (const url of imageUrls.values()) URL.revokeObjectURL(url);
    imageUrls.clear();
  });
  window.addEventListener("pageshow", event => {
    if (event.persisted) {
      if ($("image-dialog").open) $("image-dialog").close();
      void restoreSession();
    }
  });
  function captureUUID() {
    // getRandomValues also works on HTTP LAN, where randomUUID is unavailable.
    const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2,"0")).join("");
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  // Manual uploads use the same automatic analysis queue as camera captures.
  $("open-upload").addEventListener("click",() => {
    $("upload-error").textContent = ""; $("upload-message").textContent = "";
    $("upload-dialog").showModal(); $("upload-file").focus();
  });
  $("close-upload").addEventListener("click",() => { if (!uploadBusy) $("upload-dialog").close(); });
  $("upload-dialog").addEventListener("cancel",event => { if (uploadBusy) event.preventDefault(); });
  for (const id of ["upload-file", "upload-plant", "upload-name"]) $(id).addEventListener("change",() => { uploadIdentity = null; });
  $("upload-form").addEventListener("submit",async event => {
    event.preventDefault(); if (uploadBusy || session?.user.role !== "admin") return;
    const file = $("upload-file").files?.[0], plantId = $("upload-plant").value.trim(), plant = $("upload-name").value.trim();
    $("upload-error").textContent = "";
    if (!file || !/\.jpe?g$/i.test(file.name) || (file.type && file.type !== "image/jpeg")) {
      $("upload-error").textContent = "Escolha a fotografia original em formato JPEG (.jpg ou .jpeg)."; return;
    }
    if (file.size > 10*1024*1024) { $("upload-error").textContent = "A foto deve ter no máximo 10 MB e 20 megapixels."; return; }
    if (plantId && !/^[A-Za-z0-9_.:-]+$/.test(plantId)) { $("upload-error").textContent = "No identificador use letras sem acento, números, ponto, dois-pontos, hífen ou sublinhado."; return; }
    const run = generation;
    uploadBusy = true; $("upload-submit").disabled = true; $("close-upload").disabled = true;
    for (const id of ["upload-file", "upload-plant", "upload-name"]) $(id).disabled = true;
    $("upload-message").textContent = "Enviando a foto… Aguarde a confirmação.";
    try {
      // Keep identity after an ambiguous network failure: retry cannot create a duplicate photo.
      uploadIdentity ||= {id:captureUUID(),at:new Date().toISOString()};
      const headers = {"Content-Type":"image/jpeg","X-Capture-Id":uploadIdentity.id,"X-Captured-At":uploadIdentity.at};
      if (plantId) headers["X-Plant-Id"] = plantId;
      if (plant) { headers["X-Plant-Name"] = encodeURIComponent(plant); headers["X-Plant-Name-Encoding"] = "percent-utf8"; }
      const record = await api("/captures/manual", "json", "POST", file, headers);
      if (run !== generation) return;
      uploadIdentity = null; $("upload-form").reset(); $("upload-dialog").close();
      $("upload-message").textContent = "Foto recebida. A análise será feita automaticamente.";
      // The dialog polls the queue; an explicit analysis request is unnecessary.
      switchView("captures"); await showDetails(record);
    } catch (error) {
      if (run === generation && !authFailure(error)) {
        $("upload-message").textContent = "";
        $("upload-error").textContent = `${message(error)} Você pode tentar o mesmo envio novamente.`;
      }
    } finally {
      uploadBusy = false; $("upload-submit").disabled = false; $("close-upload").disabled = false;
      for (const id of ["upload-file", "upload-plant", "upload-name"]) $(id).disabled = false;
    }
  });

  async function loadDevices() {
    const run = generation; $("devices-refresh").disabled = true;
    try {
      const list = await api("/devices?limit=100&offset=0"); if (run !== generation) return;
      devices = new Map(list.map(d => [d.id,d.name]));
      $("devices-list").replaceChildren(...list.map(device => {
        const card = element("article","device-card"), badge = element("span",`received-badge ${device.is_active ? "clear-badge" : "failed-badge"}`,device.is_active ? "Envio autorizado" : "Envio desativado");
        card.append(element("span","device-icon","◉"),element("h2","",device.name),badge,element("p","detail-note",`Cadastrada em ${format(device.created_at)}`));
        return card;
      }));
      if (!list.length) $("devices-list").append(element("p","empty-state","Nenhuma câmera cadastrada."));
      $("devices-error").hidden = true;
    } catch (error) { if (run === generation && !authFailure(error)) { $("devices-error").hidden = false; $("devices-error").textContent = message(error); } }
    finally { $("devices-refresh").disabled = false; }
  }
  $("devices-refresh").addEventListener("click",loadDevices);

  function usersControls() {
    $("create-user").disabled = usersBusy;
    $("users-previous").disabled = usersBusy || usersOffset === 0;
    $("users-next").disabled = usersBusy || usersOffset+50 >= usersTotal;
    $("users-body").setAttribute("aria-busy",String(usersBusy));
  }
  async function loadUsers(target = usersOffset) {
    if (usersBusy || session?.user.role !== "admin") return;
    usersBusy = true; usersControls(); const run = generation;
    try {
      const data = await api(`/users?limit=50&offset=${target}`); if (run !== generation) return;
      usersOffset = target; usersTotal = data.total;
      $("users-body").replaceChildren(...data.items.map(user => {
        const row = element("tr"), name = element("td");
        name.append(element("strong","",user.name),element("small","",user.email));
        if (user.id === session.user.id) name.append(element("small","","Sua conta"));
        const role = element("td","",user.role === "admin" ? "Administrador" : "Consulta"), state = element("td");
        state.append(element("span",`received-badge ${user.is_active ? "clear-badge" : "failed-badge"}`,user.is_active ? "Ativa" : "Desativada"));
        const actions = element("td","table-actions"), edit = element("button","secondary","Editar"); edit.type = "button";
        edit.setAttribute("aria-label",`Editar ${user.name}`); edit.addEventListener("click",() => openUser(user)); actions.append(edit);
        if (user.id !== session.user.id) {
          const reset = element("button","secondary","Redefinir senha"); reset.type = "button"; reset.setAttribute("aria-label",`Redefinir senha de ${user.name}`);
          reset.addEventListener("click",() => { resetUser = user; $("reset-password").value = ""; $("reset-error").textContent = ""; $("reset-user-name").textContent = `${user.name} · ${user.email}`; $("reset-dialog").showModal(); $("reset-password").focus(); }); actions.append(reset);
        }
        row.append(name,role,state,actions); return row;
      }));
      $("users-page").textContent = `Página ${Math.floor(usersOffset/50)+1} · ${usersTotal} usuário(s)`; $("users-error").hidden = true;
    } catch (error) { if (run === generation && !authFailure(error)) { $("users-error").hidden = false; $("users-error").textContent = message(error); } }
    finally { if (run === generation) { usersBusy = false; usersControls(); } }
  }
  function openUser(user = null) {
    editUser = user; $("user-form").reset(); $("user-error").textContent = "";
    $("user-title").textContent = user ? "Editar usuário" : "Novo usuário";
    $("user-name").value = user?.name || ""; $("user-email").value = user?.email || ""; $("user-email").disabled = !!user;
    $("user-role").value = user?.role || "viewer"; $("user-role").disabled = user?.id === session.user.id;
    $("user-active-label").hidden = !user; $("user-active").checked = user?.is_active ?? true; $("user-active").disabled = user?.id === session.user.id;
    for (const id of ["user-password", "user-password-label", "user-password-hint"]) $(id).hidden = !!user;
    $("user-password").required = !user; $("user-password").value = "";
    $("user-submit").textContent = user ? "Salvar alterações" : "Criar usuário"; $("user-submit").disabled = false;
    $("user-dialog").showModal(); $("user-name").focus();
  }
  $("create-user").addEventListener("click",() => openUser());
  $("users-previous").addEventListener("click",() => loadUsers(Math.max(0,usersOffset-50)));
  $("users-next").addEventListener("click",() => loadUsers(usersOffset+50));
  $("close-user").addEventListener("click",() => { if (!userMutationBusy) $("user-dialog").close(); });
  $("user-dialog").addEventListener("cancel",event => { if (userMutationBusy) event.preventDefault(); });
  $("user-dialog").addEventListener("close",() => { $("user-password").value = ""; });
  $("user-form").addEventListener("submit",async event => {
    event.preventDefault(); if ($("user-submit").disabled) return;
    const user = editUser, run = generation; userMutationBusy = true; $("close-user").disabled = true;
    $("user-submit").disabled = true; $("user-error").textContent = "";
    try {
      const data = {name:$("user-name").value.trim(),role:$("user-role").value};
      if (user) { if (user.id === session.user.id) delete data.role; else data.is_active = $("user-active").checked; }
      else { data.email = $("user-email").value.trim(); data.password = $("user-password").value; }
      const saved = await api(user ? `/users/${user.id}` : "/users", "json", user ? "PATCH" : "POST", data);
      if (run !== generation) return;
      if (saved.id === session.user.id) { session.user = {...session.user,...saved}; showSession(); }
      $("user-dialog").close(); $("users-notice").textContent = user ? "Alterações salvas. Mudanças de permissão encerram as sessões da pessoa." : "Usuário criado. Ele já pode entrar no painel.";
      await loadUsers(user ? usersOffset : 0);
    } catch (error) { if (run === generation && !authFailure(error)) $("user-error").textContent = message(error); }
    finally { userMutationBusy = false; $("user-submit").disabled = false; $("close-user").disabled = false; }
  });
  $("close-reset").addEventListener("click",() => { if (!resetMutationBusy) $("reset-dialog").close(); });
  $("reset-dialog").addEventListener("cancel",event => { if (resetMutationBusy) event.preventDefault(); });
  $("reset-dialog").addEventListener("close",() => { $("reset-password").value = ""; });
  $("reset-form").addEventListener("submit",async event => {
    event.preventDefault(); if (!resetUser || $("reset-submit").disabled) return;
    const run = generation; resetMutationBusy = true; $("close-reset").disabled = true; $("reset-submit").disabled = true; $("reset-error").textContent = "";
    try {
      await api(`/users/${resetUser.id}/reset-password`, "json", "POST", {password:$("reset-password").value});
      if (run !== generation) return;
      $("reset-dialog").close(); $("users-notice").textContent = "Senha redefinida. A pessoa deve entrar novamente com a nova senha.";
    } catch (error) { if (run === generation && !authFailure(error)) $("reset-error").textContent = message(error); }
    finally { resetMutationBusy = false; $("reset-submit").disabled = false; $("close-reset").disabled = false; }
  });
  $("password-form").addEventListener("submit",async event => {
    event.preventDefault(); if ($("password-submit").disabled) return;
    $("password-error").textContent = "";
    if ($("new-password").value !== $("confirm-password").value) { $("password-error").textContent = "As novas senhas não são iguais."; return; }
    const run = generation; $("password-submit").disabled = true;
    try {
      await api("/users/me/password", "json", "POST", {current_password:$("current-password").value,new_password:$("new-password").value});
      if (run === generation) { endSession("Senha alterada. Entre novamente com a nova senha."); $("login-password").focus(); }
    } catch (error) { if (run === generation && !authFailure(error)) $("password-error").textContent = message(error); }
    finally { $("password-submit").disabled = false; }
  });

  window.addEventListener("offline",() => connection(false));
  window.addEventListener("online",() => { if (session) switchView(view); else void restoreSession(); });
  async function restoreSession() {
    $("login-button").disabled = true; $("login-button").textContent = "Verificando sessão…";
    try { session = await api("/auth/me"); generation++; showSession(); offset = 0; await switchView("captures"); }
    catch (error) { endSession(error.status === 401 ? "" : message(error)); }
    finally { $("login-button").disabled = false; $("login-button").textContent = "Entrar no painel →"; }
  }
  void restoreSession();
})();
