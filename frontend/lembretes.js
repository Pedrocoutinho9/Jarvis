// ---- Lembretes e timers: indicador na barra do topo e alerta falado na hora certa.
// Carregado depois do script principal do index.html (usa say, fetchAudio, line, clearLine, setState, busy...).
// O HUD consulta /api/lembretes/estado a cada 2 s; se a página estiver fechada, o macOS
// mostra a notificação sozinho. Clicar no indicador lista os pendentes na legenda.

document.head.append(Object.assign(document.createElement('style'), {textContent: `
.tele .lemb{cursor:pointer}
.tele .lemb b{max-width:16ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;vertical-align:bottom}
.tele .lemb.vazio b{color:var(--holo-fraco)}
.tele .lemb.alerta b,.tele .lemb.alerta span{color:var(--rubi)}
.tele .lemb.alerta{animation:lembPisca .6s steps(2) 8}
@keyframes lembPisca{50%{opacity:.25}}
@media (prefers-reduced-motion: reduce){.tele .lemb.alerta{animation:none}}
`}));

const lembBox = mk('div', 'lemb vazio');
lembBox.setAttribute('role', 'button'); lembBox.tabIndex = 0;
lembBox.title = 'Lembretes e timers pendentes';
lembBox.append(mk('span', null, 'Lembretes'), mk('b', null, '—'));
(document.querySelector('.tele') || document.querySelector('.topo') || document.body).append(lembBox);

let lembItens = [];
const pad = n => String(n).padStart(2, '0');
function lembRestante(ms){
  const s = Math.max(0, Math.round(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}
function lembHora(d){
  const hoje = new Date(), am = new Date(); am.setDate(am.getDate() + 1);
  const hora = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.toDateString() === hoje.toDateString()) return hora;
  if (d.toDateString() === am.toDateString()) return 'amanhã ' + hora;
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${hora}`;
}
function lembRender(){
  if (lembBox.classList.contains('alerta')) return;
  const b = lembBox.querySelector('b'), lbl = lembBox.querySelector('span');
  const prox = lembItens[0];
  lembBox.classList.toggle('vazio', !prox);
  if (!prox){ lbl.textContent = 'Lembretes'; b.textContent = '—'; return; }
  const quando = new Date(prox.quando), extra = lembItens.length > 1 ? ` +${lembItens.length - 1}` : '';
  if (prox.tipo === 'timer'){
    lbl.textContent = 'Timer' + (prox.texto ? ' · ' + prox.texto : '');
    b.textContent = lembRestante(quando - Date.now()) + extra;
  } else {
    lbl.textContent = 'Lembrete · ' + lembHora(quando);
    b.textContent = prox.texto + extra;
  }
}
setInterval(lembRender, 1000);

lembBox.onclick = lembBox.onkeydown = e => {
  if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return;
  if (busy || running) return;
  const txt = lembItens.length
    ? lembItens.map(i => i.tipo === 'timer'
        ? `timer${i.texto ? ' ' + i.texto : ''} (${lembRestante(new Date(i.quando) - Date.now())})`
        : `${lembHora(new Date(i.quando))} ${i.texto}`).join(' · ')
    : 'Nenhum lembrete ou timer pendente.';
  line('LEMBRETES', txt); setTimeout(() => { if (!busy && !running) clearLine(); }, 6000);
};

// bipe curto antes da fala (só se o áudio já foi liberado por um clique)
function lembBipe(){
  try {
    if (!actx || actx.state !== 'running') return;
    [0, .22].forEach(t0 => {
      const o = actx.createOscillator(), g = actx.createGain(), t = actx.currentTime + t0;
      o.frequency.value = 880; o.type = 'sine';
      g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(.18, t + .02); g.gain.linearRampToValueAtTime(0, t + .16);
      o.connect(g); g.connect(actx.destination); o.start(t); o.stop(t + .18);
    });
  } catch (e) {}
}

const lembFila = [];
let lembFalando = false;
async function lembAlerta(ev){
  lembFila.push(ev);
  if (lembFalando) return;
  lembFalando = true;
  try {
    while (lembFila.length){
      const a = lembFila.shift();
      lembBox.classList.add('alerta');
      lembBox.querySelector('span').textContent = a.tipo === 'timer' ? 'Timer' : 'Lembrete';
      lembBox.querySelector('b').textContent = a.texto || 'terminou';
      // espera a resposta em andamento acabar (até 30 s) para não falar por cima
      for (let i = 0; (busy || running) && i < 60; i++) await wait(500);
      busy = true;
      if (typeof pauseRec === 'function') pauseRec();
      lembBipe(); await wait(500);
      await say(a.say, await fetchAudio(a.say));
      await wait(1200); clearLine();
      busy = false; lastActive = Date.now();
      if (typeof resumeRec === 'function') resumeRec();
      if (!listening) setState('espera');
      setTimeout(() => { lembBox.classList.remove('alerta'); lembRender(); }, 4000);
    }
  } finally { lembFalando = false; busy = false; }
}

// consulta curta a cada 2 s (uma conexão sempre aberta travava o --reload do servidor)
let lembSeq = -1;
async function lembConsultar(){
  try {
    const r = await fetch('/api/lembretes/estado?desde=' + lembSeq, {cache: 'no-store'});
    if (r.ok){
      const d = await r.json();
      if (lembSeq >= 0) (d.alertas || []).forEach(lembAlerta);
      if (d.seq < lembSeq) lembSeq = -1; // servidor reiniciou: recomeça a contagem
      else lembSeq = d.seq;
      lembItens = d.itens || []; lembRender();
    }
  } catch (e) {}
  setTimeout(lembConsultar, 2000);
}
lembConsultar();
