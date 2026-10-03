// ---- Briefing automático de manhã (backend/autobriefing.py decide quando).
// O HUD pergunta ao servidor ao abrir e a cada minuto; se for a vez, roda o briefing sozinho e avisa que terminou.
// Sem um clique o Safari não deixa a página tocar som: nesse caso cada fala sai pelos alto-falantes do Mac
// (/api/falar-local, via afplay) e o <audio> toca mudo, o que o Safari permite, só para o reator acompanhar a voz.
// Usa do index.html: runBriefing, fetchAudio, play, player, silentWav, envNow, live, running, line.
let localVoice = false;

const _fetchAudio = fetchAudio;
fetchAudio = async text => { const a = await _fetchAudio(text); if (a) a.text = text; return a; };

const _play = play;
play = async audio => {
  if (!localVoice || !audio.text) return _play(audio);
  player.muted = true; player.src = audio.url;
  live = false; envNow = audio.env;
  player.play().catch(() => {});
  try {
    const r = await fetch('/api/falar-local', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                               body: JSON.stringify({text: audio.text})});
    if (!r.ok) throw new Error('falar-local HTTP ' + r.status);
  } finally { player.pause(); player.muted = false; envNow = null; }
};

// depois de qualquer clique o navegador já pode tocar som: volta a usar a voz da página
addEventListener('pointerdown', () => { localVoice = false; }, true);

async function canPlaySound(){
  try { player.muted = false; player.src = silentWav(); await player.play(); unlocked = true; return true; }
  catch (e) { return false; }
}

let autoBusy = false;
async function autoBriefing(){
  if (autoBusy || running) return;
  autoBusy = true;
  try {
    const r = await fetch('/api/auto-briefing/pendente');
    if (!r.ok || !(await r.json()).rodar) return;
    localVoice = !(await canPlaySound());
    if (localVoice) console.info('Safari bloqueou o som sem clique: falando pelo Mac (afplay)');
    const job = runBriefing(), started = running; // runBriefing desiste na hora se o HUD estiver ocupado
    await job;
    if (started) await fetch('/api/auto-briefing/feito', {method: 'POST'});
  } catch (e) { console.warn('briefing automático falhou:', e); }
  finally { autoBusy = false; }
}

if (new URLSearchParams(location.search).has('auto')) window.history.replaceState(null, '', location.pathname);
setTimeout(autoBriefing, 1500); // deixa o wake.js e o resto do HUD subirem antes
setInterval(autoBriefing, 60000);
