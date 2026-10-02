// ---- Palavra de ativação "Jarvis": o microfone fica sempre ligado, mas só age quando ouve "Jarvis".
// Usa o reconhecimento de voz do navegador (Safari/Chrome) em modo contínuo. Carregado depois do
// script principal do index.html, de onde vêm converse, runBriefing, line, setState, initAudio etc.
// Para não ouvir a própria voz, o reconhecimento é desligado enquanto o Jarvis fala (pauseRec/resumeRec
// são chamados pelo briefing e pela conversa) e o que chega logo depois de religar é descartado.

// "Jarvis" e como o reconhecimento em português costuma escrever: Jarbas, Járvis, Jarves, Javis...
const WAKE_RE = /(?:\b(?:ei|ok|oi|olá|ô)[\s,]+)?\bj[aáe]r?[vb][aeií]s\b[\s,.!?]*/i;
const SILENCE_MS = 1300;   // Safari às vezes demora a marcar o resultado como final: pausa = fim da frase
const FOLLOW_UP_MS = 10000; // depois de uma resposta, dá para continuar a conversa sem repetir "Jarvis"
const AWAKE_MS = 8000;     // depois de só "Jarvis", espera o comando por alguns segundos
const ECHO_MS = 700;       // descarta o que chega logo após religar o microfone (cauda da própria voz)
const MIC_PREF = 'jarvis-mic';

LABEL.ouvindo = 'DIGA “JARVIS”';
LABEL.atento = 'ESTOU OUVINDO';
document.head.append(Object.assign(document.createElement('style'), {
  textContent: 'body[data-s="atento"] .modo{color:var(--ouro)}'
}));

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, listening = false, paused = false;
let awakeUntil = 0, startedAt = 0, consumed = 0, silenceTimer = null, awakeTimer = null, fails = 0;

const isAwake = () => Date.now() < awakeUntil;
function setAwake(ms){
  awakeUntil = ms ? Date.now() + ms : 0;
  clearTimeout(awakeTimer);
  if (!listening || running || busy) return;
  setState(ms ? 'atento' : 'ouvindo');
  if (ms) awakeTimer = setTimeout(() => { if (listening && !running && !busy && state === 'atento') setState('ouvindo'); }, ms);
}

// Decide o que fazer com uma frase reconhecida. Devolve true se ela foi usada.
function handle(text){
  const m = text.match(WAKE_RE);
  if (!m && !isAwake()) return false;
  const before = m ? text.slice(0, m.index) : text, after = m ? text.slice(m.index + m[0].length) : '';
  const q = (after.trim() || before.trim()).replace(/^[\s,.!?]+|[\s,]+$/g, '');
  if (/\bbom dia\b/i.test(text) && q.replace(/[^a-zà-ú ]/gi, '').trim().toLowerCase() === 'bom dia'){
    setAwake(0); runBriefing(); return true;
  }
  if (q.length > 2){ setAwake(0); converse(q); return true; }
  // só "Jarvis": fica atento esperando o comando
  line('JARVIS', 'Pois não, senhor?'); setAwake(AWAKE_MS);
  return true;
}

function startRec(){
  if (!SR) return;
  const r = rec = new SR();
  r.lang = 'pt-BR'; r.continuous = true; r.interimResults = true;
  consumed = 0; startedAt = Date.now();
  r.onstart = () => { fails = 0; };
  r.onresult = e => {
    if (r !== rec || paused || running || busy) return;
    if (Date.now() - startedAt < ECHO_MS){ consumed = e.results.length; return; }
    let text = '', final = false;
    for (let i = Math.max(e.resultIndex, consumed); i < e.results.length; i++){
      text += e.results[i][0].transcript;
      if (e.results[i].isFinal) final = true;
    }
    text = text.trim();
    if (!text) return;
    const relevant = WAKE_RE.test(text) || isAwake();
    if (relevant) line('VOCÊ', text, 'voce'); // conversa de fundo não aparece na tela
    const finish = () => {
      clearTimeout(silenceTimer);
      if (r !== rec || paused || running || busy) return;
      consumed = e.results.length;
      if (handle(text) && !running && !busy) clearLineSoon();
    };
    clearTimeout(silenceTimer);
    if (final) finish();
    else if (relevant) silenceTimer = setTimeout(finish, SILENCE_MS);
  };
  r.onerror = e => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed'){
      stopListening(false);
      needGesture('Toque em qualquer lugar para ativar o microfone. Se não funcionar, permita o microfone para este site nos Ajustes do Safari e ative o Ditado do Mac.');
    } else if (e.error === 'audio-capture'){
      stopListening(false);
      line('JARVIS', 'Nenhum microfone encontrado, senhor. Conecte os AirPods e toque em Microfone.');
    }
  };
  r.onend = () => {
    if (r !== rec || !listening || paused) return;
    // o Safari encerra o modo contínuo de tempos em tempos: religa, com espera crescente se falhar em sequência
    fails++;
    setTimeout(() => { if (r === rec && listening && !paused) startRec(); }, fails > 3 ? 2000 : 250);
  };
  try { r.start(); } catch (e) {}
}
let clearTimer = null;
function clearLineSoon(){
  clearTimeout(clearTimer);
  clearTimer = setTimeout(() => { if (!running && !busy && !isAwake()) clearLine(); }, 2500);
}

function pauseRec(){
  paused = true; clearTimeout(silenceTimer);
  const r = rec; rec = null;
  if (r) try { r.stop(); } catch (e) {}
}
function resumeRec(){
  paused = false;
  if (!listening) return;
  startRec();
  // logo depois de uma resposta dá para seguir a conversa sem dizer "Jarvis" de novo
  setAwake(Date.now() - lastActive < 2000 ? FOLLOW_UP_MS : 0);
}
function startListening(){
  if (!SR){ line('JARVIS', 'Este navegador não suporta reconhecimento de voz. Use Safari ou Chrome.'); return false; }
  listening = true; paused = false;
  micBtn.setAttribute('aria-pressed', 'true');
  if (!running && !busy) setState('ouvindo');
  startRec();
  return true;
}
function stopListening(remember = true){
  listening = false; paused = false; setAwake(0);
  const r = rec; rec = null;
  if (r) try { r.abort(); } catch (e) {}
  micBtn.setAttribute('aria-pressed', 'false');
  if (remember) try { localStorage.setItem(MIC_PREF, 'off'); } catch (e) {}
  if (!running && !busy) setState('espera');
}

micBtn.onclick = () => {
  initAudio(); // libera o áudio enquanto há um clique do usuário
  if (listening) return stopListening();
  try { localStorage.removeItem(MIC_PREF); } catch (e) {}
  if (startListening()) line('JARVIS', 'Às suas ordens. Diga “Jarvis” e o que precisa.');
};

// O Safari só libera o som (e às vezes o microfone) depois de um gesto do usuário: o primeiro clique ou
// tecla em qualquer lugar da página destrava o áudio e liga a escuta.
function needGesture(msg){
  if (msg) line('JARVIS', msg);
  const go = ev => {
    removeEventListener('click', go, true); removeEventListener('keydown', go, true);
    initAudio();
    if (!listening && ev.target !== micBtn && micPref()) startListening();
  };
  addEventListener('click', go, true); addEventListener('keydown', go, true);
}
function micPref(){ try { return localStorage.getItem(MIC_PREF) !== 'off'; } catch (e) { return true; } }

// Ao abrir a página: no Safari a voz do Jarvis só sai depois de um clique (destrava o <audio>), então
// a escuta espera o primeiro clique ou tecla; nos outros navegadores já liga direto.
if (SAFARI && micPref()) needGesture('Toque em qualquer lugar para ativar o Jarvis, senhor.');
else { needGesture(); if (micPref()) startListening(); }
