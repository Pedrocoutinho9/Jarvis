"""Lembretes e timers do Jarvis: guardados num JSON local e disparados por um laço em segundo plano.

Ficam em ~/Library/Application Support/Jarvis/lembretes.json (ou em JARVIS_DATA_DIR), fora do repositório,
e sobrevivem a reinícios do servidor: o que venceu com o servidor desligado dispara assim que ele volta.
Na hora certa, o HUD fala o alerta (via /api/lembretes/eventos) e o macOS mostra uma notificação, para o
aviso chegar mesmo com a página fechada. Fuso em JARVIS_TZ (padrão America/Recife).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import google_data
from .memory import DATA_DIR

FILE = DATA_DIR / "lembretes.json"
TZ = ZoneInfo(os.getenv("JARVIS_TZ", "America/Recife"))
USER_TITLE = os.getenv("USER_TITLE", "senhor")
NOTIFY_ON = os.getenv("REMINDER_NOTIFY", "on").lower() != "off"  # notificação do macOS
MAX_DONE = 30            # lembretes já disparados/cancelados guardados como histórico
MAX_LATE_H = 24          # vencido há mais que isso (servidor desligado): avisa como perdido, sem alarde
_lock = threading.Lock()
_alerts: list = []       # alertas recentes (seq, evento), lidos pelo HUD em /api/lembretes/estado
_seq = [0]


def now() -> datetime:
    return datetime.now(TZ)


# ---- armazenamento ----
def _load() -> list:
    try:
        return json.loads(FILE.read_text(encoding="utf-8")).get("itens", [])
    except (FileNotFoundError, ValueError, AttributeError):
        return []


def _save(items: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    pend = [i for i in items if i["status"] == "pendente"]
    done = [i for i in items if i["status"] != "pendente"][-MAX_DONE:]
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"itens": pend + done}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FILE)
    os.chmod(FILE, 0o600)


def pending() -> list:
    return sorted((i for i in _load() if i["status"] == "pendente"), key=lambda i: i["quando"])


def add(texto: str, quando: datetime, tipo: str = "lembrete", duracao_s: int = 0) -> dict:
    with _lock:
        items = _load()
        item = {"id": max((i["id"] for i in items), default=0) + 1, "tipo": tipo,
                "texto": " ".join(str(texto).split())[:200], "quando": quando.isoformat(timespec="seconds"),
                "duracao_s": int(duracao_s), "criado": now().isoformat(timespec="seconds"), "status": "pendente"}
        items.append(item)
        _save(items)
    return item


def _set(item_id: int, **fields) -> None:
    with _lock:
        items = _load()
        for i in items:
            if i["id"] == item_id:
                i.update(fields)
        _save(items)


async def _to_calendar(item: dict) -> str:
    """Cria o evento do lembrete no Google Agenda. Devolve a frase sobre o resultado."""
    try:
        ev = await google_data.create_event(item["texto"], datetime.fromisoformat(item["quando"]))
    except google_data.NotConnected:
        return ("Não foi para a agenda: falta permissão de escrita. Diga ao usuário para reconectar em "
                "http://127.0.0.1:8000/google/conectar (o lembrete falado continua valendo).")
    except Exception as e:
        return f"Não consegui pôr na agenda ({type(e).__name__}); o lembrete falado continua valendo."
    _set(item["id"], evento=ev["id"])
    return "Também criei o evento no Google Agenda."


async def _drop_events(items: list) -> str:
    """Apaga da agenda os eventos que o Jarvis criou para lembretes cancelados."""
    ok = 0
    for i in items:
        if i.get("evento"):
            try:
                ok += await google_data.delete_event(i["evento"])
            except Exception as e:
                print(f"[jarvis] não apagou o evento {i['evento']}: {e}")
    return f" Removi {ok} evento(s) da agenda." if ok else ""


def cancel(qual) -> list:
    """Cancela pelo id, pelo texto mais parecido, ou 'todos' / 'timer' / 'lembrete'."""
    q = _norm(qual)
    with _lock:
        items = _load()
        pend = [i for i in items if i["status"] == "pendente"]
        if q.isdigit():
            gone = [i for i in pend if i["id"] == int(q)]
        elif q in ("todos", "tudo", "todos os lembretes", "todos os timers"):
            gone = pend if "timer" not in q else [i for i in pend if i["tipo"] == "timer"]
        elif q in ("timer", "o timer", "cronometro"):
            timers = [i for i in pend if i["tipo"] == "timer"]
            gone = timers[-1:]  # o mais recente
        else:
            gone = [i for i in pend if q and (q in _norm(i["texto"]) or _norm(i["texto"]) in q)]
            if not gone:  # "o das 18h", "o de 10 minutos"
                gone = [i for i in pend if q and q in _norm(_describe(i))]
        for i in gone:
            i["status"] = "cancelado"
        if gone:
            _save(items)
    return gone


# ---- interpretação de horários falados ----
def _norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9:/+,.\- ]+", " ", s)).strip()


NUMS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
        "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30,
        "quarenta": 40, "quarenta e cinco": 45, "cinquenta": 50, "noventa": 90}
UNITS = {"s": 1, "seg": 1, "segundo": 1, "segundos": 1, "m": 60, "min": 60, "mins": 60, "minuto": 60,
         "minutos": 60, "h": 3600, "hr": 3600, "hora": 3600, "horas": 3600, "dia": 86400, "dias": 86400}
DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
_DUR = re.compile(r"(\d+(?:[.,]\d+)?)\s*(segundos|segundo|seg|s|minutos|minuto|mins|min|m|horas|hora|hr|h|dias|dia)\b")


def parse_duration(text) -> int:
    """'10 minutos', '1h30', 'meia hora', '90s', 'uma hora e meia' -> segundos (0 se não entender)."""
    t = _norm(text)
    t = t.replace("meia hora", "30 minutos").replace("hora e meia", "hora 30 minutos")
    for w in sorted(NUMS, key=len, reverse=True):
        t = re.sub(rf"\b{w}\b", str(NUMS[w]), t)
    t = re.sub(r"(\d+)h(\d{1,2})\b", r"\1h \2min", t)  # 1h30
    total = sum(float(n.replace(",", ".")) * UNITS[u] for n, u in _DUR.findall(t))
    if not total and re.fullmatch(r"\+?\d+", t.strip()):
        total = int(t.strip().lstrip("+")) * 60  # número solto: minutos
    return int(total)


def parse_when(text, base: datetime | None = None):
    """Horário falado -> datetime no fuso do Jarvis, ou None. Aceita ISO, '18h', '18:30', 'amanhã 9h',
    'sexta 14h', '25/12 10h', '6 da tarde', 'meio-dia', e durações relativas ('em 20 minutos')."""
    base = base or now()
    raw = str(text).strip()
    try:  # ISO vindo do modelo
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(TZ) if dt.tzinfo else dt.replace(tzinfo=TZ)
    except ValueError:
        pass
    t = _norm(raw)
    t = t.replace("meio-dia", "12:00").replace("meio dia", "12:00").replace("meia-noite", "0:00").replace("meia noite", "0:00")
    if re.match(r"(\+|em |daqui|dentro de )", t):  # "em 1h30" é duração, não 1:30 da manhã
        secs = parse_duration(t)
        if secs:
            return base + timedelta(seconds=secs)
    clock = re.search(r"\b(\d{1,2})\s*(?::|h)\s*(\d{2})?\b|\bas (\d{1,2})\b|\b(\d{1,2})(?= da (?:manha|tarde|noite|madrugada))", t)
    if not clock:
        secs = parse_duration(t)
        return base + timedelta(seconds=secs) if secs else None
    hh = int(next(g for g in (clock.group(1), clock.group(3), clock.group(4)) if g))
    mm = int(clock.group(2) or 0)
    if re.search(r"da (tarde|noite)", t) and hh < 12:
        hh += 12
    if hh > 23 or mm > 59:
        return None
    day = base.date()
    explicit = True
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t) or None
    d = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m:
        day = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    elif d:
        y = int(d.group(3)) if d.group(3) else base.year
        y += 2000 if y < 100 else 0
        day = datetime(y, int(d.group(2)), int(d.group(1))).date()
        if not d.group(3) and day < base.date():
            day = day.replace(year=y + 1)
    elif "depois de amanha" in t:
        day += timedelta(days=2)
    elif "amanha" in t:
        day += timedelta(days=1)
    elif any(w in t for w in DIAS):
        wd = next(i for i, w in enumerate(DIAS) if w in t)
        day += timedelta(days=(wd - base.weekday()) % 7 or (7 if (hh, mm) <= (base.hour, base.minute) else 0))
    else:
        explicit = "hoje" in t
    dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
    if dt <= base and not explicit:
        # "às 7" de noite quando já passou das 7 da manhã: tenta a mesma hora à tarde, senão amanhã
        if hh < 12 and not re.search(r"da (manha|madrugada)", t) and dt + timedelta(hours=12) > base:
            dt += timedelta(hours=12)
        else:
            dt += timedelta(days=1)
    return dt


def _fmt_dur(secs: int) -> str:
    h, rest = divmod(int(secs), 3600)
    m, s = divmod(rest, 60)
    parts = [f"{h} hora{'s' if h > 1 else ''}" if h else "", f"{m} minuto{'s' if m > 1 else ''}" if m else "",
             f"{s} segundo{'s' if s > 1 else ''}" if s and not h else ""]
    parts = [p for p in parts if p]
    return " e ".join(parts) or "0 segundos"


def _fmt_when(dt: datetime) -> str:
    dt = dt.astimezone(TZ)
    hora = f"{dt.hour}h" + (f"{dt.minute:02d}" if dt.minute else "")
    diff = (dt.date() - now().date()).days
    if diff == 0:
        return f"hoje às {hora}"
    if diff == 1:
        return f"amanhã às {hora}"
    if 1 < diff < 7:
        return f"{DIAS[dt.weekday()]}{'-feira' if dt.weekday() < 5 else ''} às {hora}"
    return f"{dt.day:02d}/{dt.month:02d} às {hora}"


def _describe(i: dict) -> str:
    when = datetime.fromisoformat(i["quando"])
    if i["tipo"] == "timer":
        nome = f" ({i['texto']})" if i["texto"] else ""
        return f"#{i['id']} timer de {_fmt_dur(i['duracao_s'])}{nome}, termina {_fmt_when(when)}"
    return f"#{i['id']} {i['texto']}, {_fmt_when(when)}"


def alert_text(i: dict, late_s: float = 0) -> str:
    if i["tipo"] == "timer":
        nome = f" de {i['texto']}" if i["texto"] else ""
        msg = f"{USER_TITLE.capitalize()}, o timer{nome} de {_fmt_dur(i['duracao_s'])} terminou."
    else:
        msg = f"{USER_TITLE.capitalize()}, lembrete: {i['texto']}."
    if late_s > 120:
        msg += f" Com {_fmt_dur(late_s // 60 * 60 or late_s)} de atraso: eu estava desligado."
    return msg


# ---- disparo ----
def _publish(ev: dict) -> None:
    _seq[0] += 1
    _alerts.append((_seq[0], ev))
    del _alerts[:-20]


def state(desde: int = -1) -> dict:
    """O HUD consulta a cada 2 s: lista pendente e alertas com seq > desde (desde=-1: só a seq atual).
    Consulta curta em vez de conexão aberta (SSE), que travava o --reload do uvicorn."""
    novos = [ev for n, ev in _alerts if n > desde] if desde >= 0 else []
    return {"seq": _seq[0], "itens": pending(), "alertas": novos}


async def _notify(title: str, body: str) -> None:
    if not NOTIFY_ON or sys.platform != "darwin":
        return
    script = ["on run argv", 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
              "end run"]
    cmd = ["osascript"] + [x for ln in script for x in ("-e", ln)] + [title, body]
    try:
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(p.wait(), timeout=10)
    except Exception as e:
        print(f"[jarvis] notificação falhou: {e}")


async def fire_due() -> list:
    t = now()
    with _lock:
        items = _load()
        due = [i for i in items if i["status"] == "pendente" and datetime.fromisoformat(i["quando"]) <= t]
        for i in due:
            i["status"] = "disparado"
            i["disparado"] = t.isoformat(timespec="seconds")
        if due:
            _save(items)
    for i in due:
        late = (t - datetime.fromisoformat(i["quando"])).total_seconds()
        if late > MAX_LATE_H * 3600:
            text = f"Lembrete perdido enquanto eu estava desligado: {i['texto'] or 'timer'}."
        else:
            text = alert_text(i, late)
        print(f"[jarvis] alerta: {text}")
        _publish({"type": "alerta", "id": i["id"], "tipo": i["tipo"], "texto": i["texto"], "say": text})
        await _notify("Jarvis", text)
    return due


async def loop() -> None:
    """Confere a cada segundo pelo relógio de parede: funciona mesmo depois de o Mac acordar do repouso."""
    while True:
        try:
            await fire_due()
        except Exception as e:
            print(f"[jarvis] erro nos lembretes: {type(e).__name__}: {e}")
        await asyncio.sleep(1)


# ---- ferramentas expostas ao modelo (registradas em tools.TOOLS) ----
async def criar_lembrete(texto: str, quando: str, agenda="nao") -> str:
    dt = parse_when(quando)
    if not dt:
        return f"Não entendi o horário '{quando}'. Peça ao usuário um horário claro (ex.: 18h, amanhã 9h)."
    if dt <= now():
        return "Esse horário já passou. Confirme com o usuário o dia e a hora."
    item = add(texto or "lembrete", dt)
    out = f"Lembrete criado: {_describe(item)}. Fuso: {TZ.key}."
    if _norm(agenda) in ("sim", "s", "true", "1", "yes"):
        out += " " + await _to_calendar(item)
    return out


async def criar_timer(duracao: str, nome: str = "") -> str:
    secs = parse_duration(duracao)
    if secs <= 0:
        return f"Não entendi a duração '{duracao}'. Peça algo como '10 minutos' ou '1h30'."
    if secs > 7 * 86400:
        return "Timer longo demais: para mais de uma semana, use um lembrete com data."
    item = add(nome or "", now() + timedelta(seconds=secs), tipo="timer", duracao_s=secs)
    return f"Timer criado: {_describe(item)}."


async def listar_lembretes() -> str:
    items = pending()
    if not items:
        return "Nenhum lembrete ou timer pendente."
    return "Pendentes: " + "; ".join(_describe(i) for i in items) + f". Agora: {_fmt_when(now())}."


async def cancelar_lembrete(qual: str) -> str:
    gone = cancel(qual)
    if not gone:
        return f"Nada pendente parecido com '{qual}'. " + await listar_lembretes()
    return "Cancelado: " + "; ".join(_describe(i) for i in gone) + "." + await _drop_events(gone)


TOOLS = {
    "criar_lembrete": {"fn": criar_lembrete, "efeito": True, "web": False,
                       "desc": 'agenda um lembrete que o Jarvis fala na hora marcada (e notifica no Mac). Use para '
                               '"me lembra de X às 18h / amanhã / em 20 minutos"; pedido com horário é lembrete, '
                               'não a ferramenta lembrar (memória). '
                               'args: {"texto": "ligar para a mãe", "quando": "18h"}',
                       "params": {"agenda": "exatamente sim (só se o usuário pediu para pôr na agenda) ou nao",
                                  "texto": "do que lembrar, curto, sem a palavra lembrete",
                                  "quando": "horário como o usuário disse: 18h, 18:30, amanhã 9h, sexta 14h, "
                                            "25/12 10h, em 20 minutos; ou ISO AAAA-MM-DDTHH:MM no horário local"}},
    "criar_timer": {"fn": criar_timer, "efeito": True, "web": False,
                    "desc": 'inicia um timer (contagem regressiva) que avisa em voz quando acabar. '
                            'args: {"duracao": "10 minutos", "nome": "macarrão"}',
                    "params": {"duracao": "duração: 10 minutos, 1h30, 90 segundos, meia hora",
                               "nome": "para que é o timer (pode ser vazio)"}},
    "listar_lembretes": {"fn": listar_lembretes, "efeito": True, "web": False,
                         "desc": "lista os lembretes e timers pendentes, com horário. args: {}", "params": {}},
    "cancelar_lembrete": {"fn": cancelar_lembrete, "efeito": True, "web": False,
                          "desc": 'cancela lembrete(s) ou timer pendente. args: {"qual": "ligar para a mãe"}',
                          "params": {"qual": "número (#id), parte do texto, 'timer' para o último timer ou 'todos'"}},
}
