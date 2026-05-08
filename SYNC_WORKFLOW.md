# Sincronizacao Windows + WSL + GitHub

Este repositorio deve usar o GitHub como fonte unica de verdade.

## Regra principal

- Nao sincronizar por copia de pasta.
- Sempre sincronizar por `git push` / `git pull`.

## Fluxo recomendado

### 1) No ambiente onde voce trabalhou (Windows ou WSL)

```bash
git status
git add -A
git commit -m "mensagem objetiva"
git push origin master
```

Se nao quiser commitar ainda:

```bash
git stash push -u -m "wip"
git push origin master
```

### 2) No outro ambiente (WSL ou Windows)

```bash
git fetch origin
git checkout master
git pull --rebase origin master
```

Se voce tinha `stash`:

```bash
git stash list
git stash pop
```

## Padroes para evitar conflito

- Trabalhar em branches curtas para mudancas grandes:
  - `feature/...`
  - `fix/...`
- Nunca versionar artefatos gerados:
  - `dist/`, `build/`, `dados_compartilhados/`, `__pycache__/`
- Sempre gerar `.exe` localmente apos `pull`.

## Checklist rapido antes de trocar de ambiente

1. `git status` sem mudancas pendentes importantes.
2. `git push` concluido.
3. No outro ambiente: `git pull --rebase`.

## Limpeza inicial (uma vez)

Se artefatos gerados ja estiverem versionados no historico recente, rode:

```bash
git rm -r --cached build dist dist_* appDesktop/dados_compartilhados appDesktop/windows/dist_installer
git commit -m "chore: parar de versionar artefatos locais"
git push origin master
```
