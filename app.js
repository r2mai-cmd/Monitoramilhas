const DATA_URL = "data/promocoes.json";
const grid = document.querySelector("#promo-grid");
const origin = document.querySelector("#origin-filter");
const destination = document.querySelector("#destination-filter");
const sort = document.querySelector("#sort-filter");
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
init();
