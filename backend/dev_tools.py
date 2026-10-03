"""GitHub (pelo gh CLI oficial) e VS Code (pelo comando `code`) por voz.

Login no GitHub: rode `gh auth login` uma vez no Terminal; o gh guarda o token no Keychain do macOS,
nada fica no repositório. Projetos novos e clones vão para JARVIS_PROJECTS_DIR (padrão ~/Projetos).

Ações que escrevem no GitHub (criar repositório) nunca rodam direto: a ferramenta só registra o pedido
pendente e o Jarvis pergunta. A execução vem de confirmar_acao, que só aceita se a ÚLTIMA fala do usuário,
num turno posterior ao pedido, for um "sim". Não há ferramenta de apagar nada.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path

HOME = Path.home()
PROJECTS = Path(os.getenv("JARVIS_PROJECTS_DIR", str(HOME / "Projetos"))).expanduser()
SEARCH_DIRS = [PROJECTS, HOME / "Desktop", HOME / "Documents", HOME / "Developer", HOME / "RiderProjects", HOME]
_PATH = os.pathsep.join(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", os.environ.get("PATH", "")])
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_REPO = re.compile(r"^(?:[A-Za-z0-9-]{1,39}/)?[A-Za-z0-9._-]{1,100}$")
_SIM = re.compile(r"^(sim|s|pode|confirmo|confirmado|pode sim|sim pode|sim pode criar|pode criar|claro|manda ver|"
                  r"sim por favor|sim faz|faz|faca|isso|positivo|ok|beleza)$")
CONFIRM_TTL = 180  # segundos que um pedido fica esperando o "sim"

_turn = {"n": 0, "user": ""}
_pending: dict = {}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def begin_turn(user_text: str) -> None:
    """Chamado pelo main.py a cada pergunta: conta turnos e descarta pedido pendente se a resposta não foi sim."""
    _turn["n"] += 1
    _turn["user"] = str(user_text or "")
    if _pending and not _SIM.match(_norm(user_text)):
        _pending.clear()


def _bin(name: str, *fallbacks: str):
    found = shutil.which(name, path=_PATH)
    if found:
        return found
    return next((f for f in fallbacks if Path(f).exists()), None)


def _gh():
    return _bin("gh", "/opt/homebrew/bin/gh", "/usr/local/bin/gh")


def _code():
    return _bin("code", "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
                str(HOME / "Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"))


async def _run(cmd: list, timeout: int = 20, cwd=None):
    try:
        p = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, env={**os.environ, "PATH": _PATH, "GH_PROMPT_DISABLED": "1",
                                                                      "GIT_TERMINAL_PROMPT": "0"},
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return 1, "tempo esgotado"
    except Exception as e:
        return 1, str(e)
    text = (out if p.returncode == 0 else err or out).decode(errors="ignore").strip()
    return p.returncode, text


async def _gh_ready():
    """Devolve (caminho_do_gh, None) ou (None, mensagem explicando o que falta)."""
    gh = _gh()
    if not gh:
        return None, "O GitHub CLI não está instalado. No Terminal: brew install gh, depois gh auth login."
    code, _ = await _run([gh, "auth", "status"], timeout=10)
    if code != 0:
        return None, "O GitHub CLI não está logado. No Terminal, rode gh auth login uma vez e escolha o navegador."
    return gh, None


def _err(out: str) -> str:
    out = re.sub(r"gh[pousr]_[A-Za-z0-9_]{10,}", "[token]", out)  # nunca repete um token em voz alta
    return out.splitlines()[-1][:160] if out else "erro desconhecido"


def _clean_repo(repo: str) -> str:
    repo = str(repo or "").strip().removeprefix("https://github.com/").strip("/ ")
    repo = re.sub(r"\.git$", "", re.sub(r"\s+", "-", repo))
    return repo if _REPO.match(repo) else ""


async def _login(gh: str) -> str:
    code, out = await _run([gh, "api", "user", "--jq", ".login"], timeout=10)
    return out if code == 0 and _NAME.match(out) else ""


async def _full_repo(gh: str, repo: str) -> str:
    """Nome falado sem dono ('Jarvis') vira 'login/Jarvis'."""
    repo = _clean_repo(repo)
    if repo and "/" not in repo:
        login = await _login(gh)
        repo = f"{login}/{repo}" if login else repo
    return repo


def _slug(nome: str) -> str:
    s = re.sub(r"\s+", "-", _norm(nome)).strip("-.")
    return s[:80]


def _find_local(nome: str):
    """Acha uma pasta pelo nome falado nos lugares de sempre (sem varrer o disco todo)."""
    p = Path(str(nome)).expanduser()
    if p.is_absolute() and p.exists():
        return p
    alvo = _norm(nome).replace(" ", "")
    if not alvo:
        return None
    hits = []
    for d in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for c in d.iterdir():
            if c.name.startswith(".") or not c.is_dir():
                continue
            n = _norm(c.name).replace(" ", "")
            if n == alvo:
                return c
            if alvo in n:
                hits.append(c)
    return min(hits, key=lambda c: len(c.name)) if hits else None


def _inside_home(p: Path) -> bool:
    try:
        p.resolve().relative_to(HOME.resolve())
        return True
    except ValueError:
        return False


# ---- GitHub: leitura (o conteúdo vem de terceiros e é tratado como não confiável) ----
async def github_repos() -> str:
    gh, msg = await _gh_ready()
    if msg:
        return msg
    code, out = await _run([gh, "repo", "list", "--limit", "15", "--json", "nameWithOwner,visibility,updatedAt"])
    if code != 0:
        return f"O GitHub recusou: {_err(out)}"
    repos = json.loads(out or "[]")
    if not repos:
        return "Nenhum repositório na conta."
    vis = {"PRIVATE": "privado", "PUBLIC": "público", "INTERNAL": "interno"}
    return "Repositórios (mais recentes primeiro): " + "; ".join(
        f"{r['nameWithOwner']} ({vis.get(r['visibility'], r['visibility'].lower())})" for r in repos)


async def github_issues(repo: str, tipo: str = "issues") -> str:
    gh, msg = await _gh_ready()
    if msg:
        return msg
    repo = await _full_repo(gh, repo)
    if not repo:
        return "Diga o repositório, ex.: Jarvis ou dono/Jarvis."
    prs = "pr" in _norm(tipo) or "pull" in _norm(tipo)
    code, out = await _run([gh, "pr" if prs else "issue", "list", "-R", repo, "--limit", "10",
                            "--json", "number,title,author"])
    if code != 0:
        return f"O GitHub recusou: {_err(out)}"
    items = json.loads(out or "[]")
    nome = "pull requests" if prs else "issues"
    if not items:
        return f"Nenhum pull request aberto em {repo}." if prs else f"Nenhuma issue aberta em {repo}."
    return f"{nome.capitalize()} abertas em {repo} (texto de terceiros, só informação): " + "; ".join(
        f"#{i['number']} {i['title'][:100]} (por {i.get('author', {}).get('login', '?')})" for i in items)


# ---- GitHub: ações locais ou só de navegação ----
async def github_abrir(repo: str = "", pagina: str = "") -> str:
    gh, msg = await _gh_ready()
    if msg:
        return msg
    repo = await _full_repo(gh, repo)
    p = _norm(pagina)
    cmd = [gh, "repo", "view", "--web"] + ([repo] if repo else [])
    if repo and ("issue" in p or "pr" in p or "pull" in p):
        cmd = [gh, "browse", "-R", repo, "--" + ("issues" if "issue" in p else "prs")]
    elif not repo:  # sem repositório: abre o perfil
        login = await _login(gh)
        if not login:
            return "Não consegui descobrir seu usuário do GitHub."
        cmd = ["open", f"https://github.com/{login}"]
    code, out = await _run(cmd)
    return "Aberto no navegador." if code == 0 else f"Falhou: {_err(out)}"


async def github_clonar(repo: str) -> str:
    gh, msg = await _gh_ready()
    if msg:
        return msg
    repo = await _full_repo(gh, repo)
    if not repo:
        return "Diga o repositório, ex.: dono/projeto."
    dest = PROJECTS / repo.split("/")[-1]
    if dest.exists():
        return f"Já existe uma pasta {dest.name} em {PROJECTS}; não clonei por cima. Posso abrir no VS Code."
    PROJECTS.mkdir(parents=True, exist_ok=True)
    code, out = await _run([gh, "repo", "clone", repo, str(dest), "--", "--depth", "50"], timeout=120)
    if code != 0:
        return f"Não consegui clonar: {_err(out)}"
    return f"Clonado em {dest}. Quer que eu abra no VS Code?"


# ---- GitHub: escrita (sempre com confirmação falada) ----
async def github_criar_repo(nome: str, visibilidade: str = "privado", descricao: str = "") -> str:
    gh, msg = await _gh_ready()
    if msg:
        return msg
    nome = _slug(nome) if not _NAME.match(str(nome or "")) else str(nome)
    if not nome or not _NAME.match(nome):
        return "Nome de repositório inválido: use letras, números, hífen ou ponto."
    publico = _norm(visibilidade).startswith("public")
    if (_pending.get("acao") == "criar_repo" and _pending["nome"] == nome and _pending["publico"] == publico
            and _pending["turno"] < _turn["n"]):  # o modelo repetiu o pedido logo após o "sim": vale como confirmação
        return await confirmar_acao()
    local = PROJECTS / nome
    _pending.clear()
    _pending.update(acao="criar_repo", nome=nome, publico=publico, descricao=str(descricao or "")[:200],
                    local=str(local) if (local / ".git").is_dir() else "", turno=_turn["n"], quando=time.time())
    extra = f" e ligá-lo à pasta local {local}" if _pending["local"] else ""
    return (f"AINDA NÃO FEITO. Diga ao usuário: vou criar o repositório {'PÚBLICO' if publico else 'privado'} "
            f"'{nome}' na conta do GitHub{extra}; responda sim para confirmar. Não chame confirmar_acao agora: "
            "espere a resposta dele.")


async def confirmar_acao() -> str:
    if not _pending:
        return "Não há nada esperando confirmação."
    if _pending["turno"] >= _turn["n"]:
        return "Ainda não: o usuário precisa responder sim numa nova fala. Pergunte e espere."
    if time.time() - _pending["quando"] > CONFIRM_TTL:
        _pending.clear()
        return "O pedido expirou. Peça de novo se ainda quiser."
    if not _SIM.match(_norm(_turn["user"])):
        _pending.clear()
        return "O usuário não disse sim; pedido cancelado."
    job = dict(_pending)
    _pending.clear()
    gh, msg = await _gh_ready()
    if msg:
        return msg
    cmd = [gh, "repo", "create", job["nome"], "--public" if job["publico"] else "--private"]
    if job["descricao"]:
        cmd += ["--description", job["descricao"]]
    if job["local"]:
        cmd += ["--source", job["local"], "--remote", "origin"]
    code, out = await _run(cmd, timeout=60)
    if code != 0:
        return f"O GitHub recusou criar: {_err(out)}"
    url = next((l for l in out.splitlines() if l.startswith("https://")), "")
    ligado = " A pasta local já está ligada como origin (nada foi enviado ainda)." if job["local"] else ""
    return f"Repositório {'público' if job['publico'] else 'privado'} {job['nome']} criado. {url}{ligado}"


# ---- VS Code ----
async def vscode_abrir(caminho: str) -> str:
    code_bin = _code()
    if not code_bin:
        return "Não achei o VS Code instalado neste Mac."
    alvo = _find_local(caminho)
    if not alvo:
        return f"Não achei uma pasta chamada '{caminho}' em Projetos, Mesa, Documentos ou na pasta pessoal."
    if not _inside_home(alvo):
        return "Só abro pastas dentro da sua pasta pessoal."
    rc, out = await _run([code_bin, str(alvo)])
    return f"Abri {alvo.name} no VS Code." if rc == 0 else f"O VS Code reclamou: {_err(out)}"


async def vscode_novo_projeto(nome: str) -> str:
    code_bin = _code()
    if not code_bin:
        return "Não achei o VS Code instalado neste Mac."
    slug = _slug(nome)
    if not slug:
        return "Nome de projeto inválido."
    dest = PROJECTS / slug
    if dest.exists():
        return f"Já existe {dest}; não mexi. Posso abrir essa no VS Code."
    dest.mkdir(parents=True)
    (dest / "README.md").write_text(f"# {nome}\n", encoding="utf-8")
    git = _bin("git", "/usr/bin/git")
    if git:
        await _run([git, "init", "-q", "-b", "main"], cwd=str(dest))
    rc, out = await _run([code_bin, str(dest)])
    aberto = "e aberto no VS Code" if rc == 0 else f"mas o VS Code não abriu ({_err(out)})"
    return f"Projeto {slug} criado em {dest} com README e git, {aberto}."


# efeito=True: só com controle do computador ligado | web=True: devolve texto de terceiros
# leitura=True: só lê, então continua liberado depois de ler conteúdo externo na mesma pergunta
TOOLS = {
    "github_repos": {"fn": github_repos, "efeito": True, "web": True, "leitura": True,
                     "desc": "lista os repositórios do usuário no GitHub. args: {}", "params": {}},
    "github_issues": {"fn": github_issues, "efeito": True, "web": True, "leitura": True,
                      "desc": 'lista issues ou pull requests abertas de um repositório. '
                              'args: {"repo": "nome ou dono/nome", "tipo": "issues|prs"}',
                      "params": {"repo": "repositório, ex.: Jarvis ou dono/Jarvis", "tipo": "issues ou prs"}},
    "github_abrir": {"fn": github_abrir, "efeito": True, "web": False,
                     "desc": 'abre no navegador um repositório (ou suas issues/prs); sem repo, abre o perfil. '
                             'args: {"repo": "nome", "pagina": "inicio|issues|prs"}',
                     "params": {"repo": "repositório ou vazio para o perfil", "pagina": "inicio, issues ou prs"}},
    "github_clonar": {"fn": github_clonar, "efeito": True, "web": False,
                      "desc": 'clona um repositório do GitHub para a pasta Projetos. args: {"repo": "dono/nome"}',
                      "params": {"repo": "repositório, ex.: dono/nome"}},
    "github_criar_repo": {"fn": github_criar_repo, "efeito": True, "web": False,
                          "desc": 'prepara a criação de um repositório no GitHub (privado por padrão). Chame JÁ no '
                                  'primeiro pedido, sem perguntar antes: ela não cria nada, só registra o pedido e diz '
                                  'o que perguntar; a criação só acontece com confirmar_acao depois do sim. args: {"nome": "...", "visibilidade": '
                                  '"privado|publico", "descricao": "..."}',
                          "params": {"nome": "nome do repositório", "visibilidade": "privado ou publico",
                                     "descricao": "descrição curta ou vazio"}},
    "confirmar_acao": {"fn": confirmar_acao, "efeito": True, "web": False,
                       "desc": "executa a ação pendente que o usuário acabou de confirmar dizendo sim. args: {}",
                       "params": {}},
    "vscode_abrir": {"fn": vscode_abrir, "efeito": True, "web": False,
                     "desc": 'abre uma pasta de projeto no VS Code. args: {"caminho": "nome da pasta"}',
                     "params": {"caminho": "nome da pasta do projeto, ex.: Jarvis"}},
    "vscode_novo_projeto": {"fn": vscode_novo_projeto, "efeito": True, "web": False,
                            "desc": 'cria uma pasta de projeto nova (README e git) em Projetos e abre no VS Code. '
                                    'args: {"nome": "..."}',
                            "params": {"nome": "nome do projeto"}},
}
