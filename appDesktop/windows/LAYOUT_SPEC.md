# Layout Spec (Desktop UI)

- Spec version: `1.1.0`
- Target file: `appDesktop/roteirizador_desktop/ui.py`
- Platform scope: Windows/Linux

## Baseline constants

These constants must stay synchronized with this document:

- `LAYOUT_SPEC_VERSION = "1.1.0"`
- `HELP_SECTION_MAX_HEIGHT = 220`
- `BUTTON_GRID_SPACING = 6`
- `USE_COLLAPSIBLE_SPLITTERS = False`

## Required behavior

### Help blocks

Sections:

- `Como digitar rota`
- `Dica de preenchimento da demanda`

Rules:

- Collapsible behavior enabled.
- Content wrapped in scrollable area.
- Expanded state maximum height: `220 px`.
- Collapsed state height: `0 px`.

### Splitters in version tab

Main splitters in the version editor tab must not allow panel collapse:

- Vertical main splitter.
- Horizontal input splitter.
- Horizontal output splitter.

Implementation rule:

- `setChildrenCollapsible(USE_COLLAPSIBLE_SPLITTERS)` with baseline `False`.

### Action bars (`Embarcacoes disponiveis` and `Demanda`)

Rules:

- Action controls must be a single horizontal row (`QHBoxLayout`) per section.
- Controls must stay grouped to the left, with remaining space consumed at the end (`addStretch(1)`).
- Growth in panel height must be absorbed by the table, not by gaps around the buttons.
- Section layout stretch policy:
  - `hint`: fixed
  - `table`: stretch `1`
  - `action bar`: fixed
- Compact labels required:
  - `Add barco`
  - `Exc. barco`
  - `Add linha`
  - `Exc. linha`
  - `Imp. csv`
  - `Imp. Extrato pdf`
  - `Exp. csv`

## Build guardrail

Use `appDesktop/windows/build_portable.ps1` for portable builds.
This script validates the layout baseline before running PyInstaller.

## Change control

If any baseline changes:

1. Update constants in `ui.py`.
2. Update this markdown in the same change.
3. Update `windows/validate_layout_spec.py` checks in the same change.
