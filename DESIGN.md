---
name: MiroFish
description: Swarm intelligence simulation console — upload seeds, build graphs, run agents, read reports.
colors:
  primary: "#ff4500"
  primary-hover: "#e63e00"
  ink: "#000000"
  surface: "#ffffff"
  surface-muted: "#f5f5f5"
  border: "#e5e5e5"
  text-muted: "#767676"
  text-body: "#666666"
  success: "#1a936f"
  error: "#c5283d"
  warning: "#ff5722"
typography:
  display:
    fontFamily: "'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif"
    fontSize: "clamp(2rem, 6vw, 4.5rem)"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "-0.04em"
  body:
    fontFamily: "'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
  mono:
    fontFamily: "'JetBrains Mono', ui-monospace, monospace"
    fontSize: "0.8rem"
    fontWeight: 500
    lineHeight: 1.4
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "40px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "20px"
  button-primary-hover:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
---

## Overview

MiroFish uses a **restrained product palette**: black ink on white surfaces, orange (`#FF4500`) for primary actions and live status, and gray ramps for borders and secondary text. Typography pairs **Space Grotesk** (UI and headings) with **JetBrains Mono** (labels, console chrome, step numbers). Layout is a fixed-height workbench shell with optional graph/workbench split; the home page introduces the workflow before entering Step 1.

## Colors

| Role | Token | Hex | Usage |
|------|-------|-----|-------|
| Ink | `--mf-black` | `#000000` | Headings, primary buttons, nav bar (home) |
| Surface | `--mf-white` | `#ffffff` | Page and panel backgrounds |
| Accent | `--mf-orange` | `#FF4500` | CTAs, processing dots, tags |
| Muted text | `--mf-gray-400` | `#767676` | Labels, metadata (AA on white) |
| Body secondary | `--mf-gray-500` | `#666666` | Descriptions |
| Border | `--mf-border` | `#E5E5E5` | Panels, inputs, dividers |
| Success | `--mf-success` | `#1A936F` | Completed states |
| Error | `--mf-error` | `#C5283D` | Failures |
| Warning | `--mf-warning` | `#FF5722` | In-progress pulse |

Do not introduce a second accent (e.g. indigo/purple) without mapping it into this system. `Step4Report` should migrate toward these tokens over time.

## Typography

- **Display:** Space Grotesk, clamped hero scale, `text-wrap: balance` on home title.
- **Body:** Space Grotesk, 1rem / 1.6 line-height; prose blocks cap near 65ch where possible.
- **Mono:** JetBrains Mono for step IDs, console labels, file lists, and brand wordmark.
- **Locale:** `Noto Sans SC` in stacks for Chinese; English locale may fall back to system UI for body copy.

## Elevation

Flat product UI — separation via **1px borders** and surface tints (`--mf-gray-50`, `--mf-gray-100`), not drop shadows. Modals use a single soft shadow and `--mf-z-modal` (500). Avoid glassmorphism; use opaque or near-opaque overlays.

## Components

- **Navbar (home):** 60px black bar, white mono logotype, dark-variant language switcher.
- **Workbench header:** White bar, centered layout switcher (graph / split / workbench), step indicator, status dot.
- **Console upload:** Dashed border zone, mono hints, full-width black CTA → orange on hover.
- **Step cards:** Numbered headers (01, 02) inside workflow context only — not decorative landing eyebrows.
- **History cards:** Horizontal stack with hover expand; modal for replay navigation.

## Do's and Don'ts

**Do**
- Use CSS variables from `frontend/src/styles/tokens.css`
- Provide `aria-label`, `aria-pressed`, and visible `:focus-visible` rings
- Stack panels vertically below 1024px in the workbench
- Honor `prefers-reduced-motion`

**Don't**
- Gradient text (`background-clip: text`)
- Colored side-stripe callouts (`border-left` accent bars)
- Hero metric card grids on landing
- `z-index: 9999` — use the semantic scale (`--mf-z-*`)
- Load unused fonts (Inter removed from bundle)
