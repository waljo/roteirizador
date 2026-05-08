# AGENTS

## Regra de build Windows sem instalador

Para gerar executavel portatil, sempre use:

```powershell
powershell -ExecutionPolicy Bypass -File appDesktop/windows/build_portable.ps1
```

Nao usar Inno Setup quando o pedido for "sem instalador".

## Regra de layout

Antes de build, o baseline de layout deve estar valido:

- `appDesktop/windows/LAYOUT_SPEC.md`
- `appDesktop/windows/validate_layout_spec.py`
- `appDesktop/roteirizador_desktop/ui.py`

Se qualquer baseline mudar, atualizar os tres arquivos na mesma alteracao.
