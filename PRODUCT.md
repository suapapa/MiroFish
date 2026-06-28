# MiroFish — Product Context

## Register

**product** — The primary surfaces are a multi-step simulation workbench (graph build, environment setup, simulation run, report, interaction). The home page is onboarding into that workflow, not a standalone marketing site.

## Users & Purpose

- **Who:** Researchers, analysts, and decision-makers exploring social dynamics, policy outcomes, or narrative scenarios from seed documents.
- **Job:** Upload unstructured materials (PDF, Markdown, text), describe a simulation goal, and receive a knowledge graph, agent simulation, and prediction report.
- **Context:** Desk work, focused sessions, often comparing multiple historical runs from the project database.

## Brand Personality

Precise, technical, forward-looking — a **simulation console**, not a generic SaaS dashboard. Monospace labels, high contrast black/white, orange as the single accent for action and status.

## Anti-References

- Purple-gradient AI landing pages and hero metric cards
- Inter-only generic startup UI
- Decorative glassmorphism and bounce animations
- Modal-first flows for tasks that belong inline

## Strategic Design Principles

1. **Console over campaign** — UI should feel like operating machinery, not reading a brochure.
2. **Progressive disclosure** — Five steps are visible in sequence; each step reveals only what the task needs.
3. **Graph + workbench** — The knowledge graph stays visible alongside step panels when space allows.
4. **i18n by default** — English, Korean, and Chinese; no hardcoded status strings in views.
5. **Accessible operations** — Keyboard, screen readers, and reduced motion are first-class for upload and navigation.

## Accessibility

- WCAG 2.1 AA target for text contrast and focus visibility
- Minimum 44×44px touch targets on primary controls
- `prefers-reduced-motion` respected globally

## Tech Stack (UI)

- Vue 3 + Vue Router + vue-i18n
- Vite 7
- D3 for graph visualization
- Design tokens in `frontend/src/styles/tokens.css`
