const DATA_URL = "data/promocoes.json";
const grid = document.querySelector("#promo-grid");
const origin = document.querySelector("#origin-filter");
const destination = document.querySelector("#destination-filter");
const sort = document.querySelector("#sort-filter");
const conversionList = document.querySelector("#conversion-list");
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fillOptions(select, values, label) {
  const selected = select.value;
  select.innerHTML = `<option value="">${label}</option>` + [...new Set(values.filter(Boolean))].sort().map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  select.value = selected;
}
function render(items) {
  const filtered = items.filter(p => (!origin.value || p.origem === origin.value) && (!destination.value || p.destino === destination.value));
  filtered.sort((a,b) => sort.value === "bonus" ? (b.bonus_percentual||0)-(a.bonus_percentual||0) : sort.value === "deadline" ? (a.validade||"9999").localeCompare(b.validade||"9999") : (b.detectada_em||"").localeCompare(a.detectada_em||""));
  if (!filtered.length) {
    grid.innerHTML = `<div class="empty-state"><div class="empty-icon">✈</div><h3>Nenhuma promoção ativa confirmada</h3><p>Quando o monitor identificar uma campanha válida nas fontes configuradas, ela aparecerá aqui. Não exibimos ofertas fictícias.</p><span class="empty-chip">Monitor em fase de configuração</span></div>`;
    return;
  }
  grid.innerHTML = filtered.map(p => `<article class="promo-card"><div class="promo-route"><span>${escapeHtml(p.origem)}</span><span class="route-arrow">⟶</span><span>${escapeHtml(p.destino)}</span></div><div class="promo-card-body"><div class="bonus">${Number(p.bonus_percentual)}% <small>de bônus</small></div><h3>${escapeHtml(p.titulo || `${p.origem} → ${p.destino}`)}</h3><div class="promo-meta">▦ Validade: ${escapeHtml(p.validade || "Consultar regras")}<br>♧ ${escapeHtml(p.elegibilidade || "Consulte as condições")}<br>✓ Fonte: ${escapeHtml(p.fonte_nome || "Fonte oficial")}</div><a class="button" href="${escapeHtml(p.url_fonte)}" target="_blank" rel="noopener noreferrer">Ver campanha ↗</a></div></article>`).join("");
}
async function init() {
  try {
    const response = await fetch(DATA_URL, {cache:"no-store"});
    if (!response.ok) throw new Error("Ainda sem arquivo de campanhas");
    const payload = await response.json();
    const items = (payload.promocoes || []).filter(p => p.status === "ativa" && p.url_fonte);
    fillOptions(origin, items.map(p=>p.origem), "Todos os programas");
    fillOptions(destination, items.map(p=>p.destino), "Todos os destinos");
    document.querySelector("#last-check").textContent = payload.verificado_em ? `Última verificação: ${new Date(payload.verificado_em).toLocaleString("pt-BR")}` : "Monitor em fase de configuração";
    render(items);
    [origin,destination,sort].forEach(el=>el.addEventListener("change",()=>render(items)));
  } catch {
    document.querySelector("#last-check").textContent = "Monitor em fase de configuração";
  }
}
function officialProgramUrl(candidate) {
  const title = String(candidate.titulo || "").toLowerCase();
  if (title.includes("latam pass") || title.includes("latampass")) return "https://latampass.latam.com/pt_br/promocoes";
  if (title.includes("smiles")) return "https://www.smiles.com.br/";
  if (title.includes("livelo")) return "https://www.livelo.com.br/";
  if (title.includes("azul")) return "https://www.voeazul.com.br/br/pt/ofertas";
  if (title.includes("esfera")) return "https://www.esfera.com.vc/";
  if (title.includes("dotz")) return "https://www.dotz.com.br/";
  if (title.includes("c6")) return "https://www.c6bank.com.br/";
  return null;
}
async function initRadar() {
  const radar = document.querySelector("#radar-grid");
  const status = document.querySelector("#radar-check");
  try {
    const response = await fetch("/Monitoramilhas/data/monitor_fontes.json?v=20261017-radar2", {cache:"no-store"});
    if (!response.ok) throw new Error("Relatório indisponível");
    const report = await response.json();
    const now = new Date();
    const items = (report.campanhas_aguardando_validacao || report.candidatos || [])
      .filter(p => {
        if (!p.link_resultado || !p.titulo) return false;
        if (!p.data_encerramento_detectada) return true;
        const end = new Date(p.data_encerramento_detectada);
        return Number.isNaN(end.getTime()) || end >= now;
      })
      .slice(0, 12);
    status.textContent = report.atualizado_em
      ? "Relatório atualizado em " + new Date(report.atualizado_em).toLocaleString("pt-BR")
      : "Relatório do monitor";
    if (!items.length) {
      radar.innerHTML = '<div class="empty-state"><h3>Nenhuma pista pendente recente</h3><p>O monitor não encontrou candidatos com prazo futuro ou ainda não identificado nesta execução.</p></div>';
      return;
    }
    radar.innerHTML = items.map(p => {
      const officialUrl = officialProgramUrl(p);
      const date = p.publicado_em ? new Date(p.publicado_em) : null;
      const published = date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString("pt-BR") : "Data não identificada";
      const expiry = p.data_encerramento_detectada ? new Date(p.data_encerramento_detectada).toLocaleDateString("pt-BR") : "Prazo não identificado";
      const excerpt = p.descricao || p.motivo_status || p.observacao || "Notícia encontrada; confirmar as regras oficiais.";
      const button = officialUrl
        ? '<a class="button" href="' + escapeHtml(officialUrl) + '" target="_blank" rel="noopener noreferrer">Consultar programa ↗</a>'
        : '<span class="button" aria-disabled="true">Programa oficial não identificado</span>';
      return '<article class="promo-card radar-card"><div class="promo-card-body"><span class="empty-chip">PENDENTE DE VALIDAÇÃO</span><h3>' + escapeHtml(p.titulo) + '</h3><div class="promo-meta">Notícia identificada em: ' + escapeHtml(p.publicador || p.fonte_busca || "Fonte monitorada") + '<br>Publicado em: ' + escapeHtml(published) + '<br>Prazo detectado: ' + escapeHtml(expiry) + '</div><p>' + escapeHtml(excerpt.slice(0, 420)) + '</p>' + button + '</div></article>';
    }).join("");
  } catch {
    status.textContent = "Não foi possível carregar o relatório";
    radar.innerHTML = '<div class="empty-state"><h3>Relatório ainda indisponível</h3><p>O site não conseguiu ler data/monitor_fontes.json. Atualiza a página depois que o workflow terminar.</p></div>';
  }
}
async function initConversions() {
  try {
    const response = await fetch("data/conversoes.json", {cache:"no-store"});
    if (!response.ok) throw new Error("Arquivo de conversões indisponível");
    const payload = await response.json();
    const items = (payload.conversoes || []).filter(c => c.status === "confirmada" && c.url_fonte);
    const check = document.querySelector("#conversion-check");
    check.textContent = payload.verificado_em ? `Fontes consultadas em ${new Date(payload.verificado_em + "T12:00:00").toLocaleDateString("pt-BR")}` : "Paridades confirmadas";
    if (!items.length) {
      conversionList.innerHTML = '<div class="conversion-empty">Ainda não há conversões confirmadas para exibir. Estamos priorizando informações publicadas pelos próprios programas.</div>';
      return;
    }
    conversionList.innerHTML = items.map(c => `<article class="conversion-card"><div class="conversion-pair"><span>${escapeHtml(c.origem)}</span><span class="arrow">⟶</span><span>${escapeHtml(c.destino)}</span></div><div class="conversion-rate">${escapeHtml(c.paridade)} <small>sem bônus</small></div><p class="conversion-minimum">${escapeHtml(c.condicoes || "Consulte os requisitos no programa de origem.")}</p><div class="conversion-footer"><span class="conversion-source">Fonte oficial: ${escapeHtml(c.fonte_nome)}</span><span class="conversion-date">Consultado em ${escapeHtml(new Date(c.verificado_em + "T12:00:00").toLocaleDateString("pt-BR"))}</span><a class="conversion-link" href="${escapeHtml(c.url_fonte)}" target="_blank" rel="noopener noreferrer">Conferir regras oficiais ↗</a></div></article>`).join("");
  } catch {
    document.querySelector("#conversion-check").textContent = "Conversões não carregadas";
    conversionList.innerHTML = '<div class="conversion-empty">Não foi possível carregar as conversões agora. Tenta atualizar a página em instantes.</div>';
  }
}
init();
initConversions();
initRadar();
