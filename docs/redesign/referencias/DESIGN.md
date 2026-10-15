---
name: Industrial Signage Operations
colors:
  surface: '#f8f9ff'
  surface-dim: '#d0dbed'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dee9fc'
  surface-container-highest: '#d8e3f6'
  on-surface: '#111c2a'
  on-surface-variant: '#434655'
  inverse-surface: '#26313f'
  inverse-on-surface: '#eaf1ff'
  outline: '#737686'
  outline-variant: '#c3c6d7'
  surface-tint: '#0053db'
  primary: '#004ac6'
  on-primary: '#ffffff'
  primary-container: '#2563eb'
  on-primary-container: '#eeefff'
  inverse-primary: '#b4c5ff'
  secondary: '#565e74'
  on-secondary: '#ffffff'
  secondary-container: '#dae2fd'
  on-secondary-container: '#5c647a'
  tertiary: '#943700'
  on-tertiary: '#ffffff'
  tertiary-container: '#bc4800'
  on-tertiary-container: '#ffede6'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dbe1ff'
  primary-fixed-dim: '#b4c5ff'
  on-primary-fixed: '#00174b'
  on-primary-fixed-variant: '#003ea8'
  secondary-fixed: '#dae2fd'
  secondary-fixed-dim: '#bec6e0'
  on-secondary-fixed: '#131b2e'
  on-secondary-fixed-variant: '#3f465c'
  tertiary-fixed: '#ffdbcd'
  tertiary-fixed-dim: '#ffb596'
  on-tertiary-fixed: '#360f00'
  on-tertiary-fixed-variant: '#7d2d00'
  background: '#f8f9ff'
  on-background: '#111c2a'
  surface-variant: '#d8e3f6'
typography:
  kpi-value:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 38px
    letterSpacing: -0.02em
  kpi-value-mobile:
    fontFamily: Inter
    fontSize: 26px
    fontWeight: '700'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 30px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 22px
    letterSpacing: -0.005em
  body-default:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-semibold:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
  label-default:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
  caption:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  code-mono:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1rem
  gutter-lg: 1.5rem
  margin: 1rem
  margin-md: 1.5rem
  margin-lg: 2rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system targets mission-critical operations: industrial facility managers, enterprise IT architects, and corporate communications directors deploying digital displays across manufacturing floors, corporate campuses, logistics hubs, and executive terminals.

The aesthetic follows an utilitarian, high-density corporate SaaS standard. It eliminates purely decorative visual noise, skeuomorphic flourishes, ambient glow effects, and blurred materials in favor of clarity, deterministic spatial ordering, and instantaneous state recognition.

Key behavioral attributes:
- **Operational Focus:** Maximum signal-to-noise ratio. Complex device grids, telemetry feeds, and playlist schedulers are readable at a glance.
- **Predictable Structure:** Visual hierarchy relies on structured borders, strict tabular alignments, and unambiguous interactive states.
- **Controlled Density:** Compact enough to present 20+ display endpoints per viewport without visual crowding, balanced by consistent 4px/8px incremental spacing.

## Colors

The palette establishes strict functional separation between structural navigation, operational canvas, actionable interactions, and device states.

### Core Foundation
- **Canvas Neutral (`#F4F6F9`):** The global viewport background. Provides soft contrast against crisp white content panels without eye fatigue under indoor lighting.
- **Sidebar Surface (`#0F172A`):** Deep navy reserved exclusively for primary application shell navigation, conveying structural stability and clear scope isolation.
- **Card & Component Surface (`#FFFFFF`):** High-contrast base for data widgets, monitoring tables, forms, and dialogs.
- **Structural Border (`#E4E8EE`):** Uniform 1px perimeter definition on all containers, dividers, and input borders.

### Interactive & Typography
- **Primary Interactive (`#2563EB`):** High-efficiency Royal Blue for primary calls-to-action, focused input perimeters, active segmented buttons, and selection indicators.
- **Text Primary (`#0F172A`):** Deep slate-black delivering maximum WCAG AAA legibility against white containers.
- **Text Secondary (`#5B6676`):** Balanced neutral slate for labels, metadata, device serials, and timestamps.

### Semantic Telemetry Tokens
Device and network states must never rely on color alone; every semantic status token is paired with an inline iconography asset and explicit status copy.
- **Online / Healthy:** Text/Icon `#15803D` on Pill Background `#E8F5EC` (Border: `#C6E7D0`)
- **Warning / Degraded:** Text/Icon `#B45309` on Pill Background `#FEF3E2` (Border: `#FCDCA5`)
- **Offline / Error:** Text/Icon `#B91C1C` on Pill Background `#FDECEC` (Border: `#F8B4B4`)

## Typography

The type scale is optimized for high-density administrative interfaces and operational command centers.

### Numeric & Metric Rendering
All KPI figures, network throughput readouts, uptime percentages, and MAC/IP addresses must use tabular numbers (`font-feature-settings: 'tnum' 1`). This prevents layout jitter during real-time telemetry refreshes.

### Hierarchy & Role Distribution
- **KPI Value (32px / Bold):** Used strictly for high-level operations telemetry (e.g., total active screens, bandwidth consumption, pending deployment alerts).
- **Headline Medium (18px / Semibold):** Module container headers and primary screen view titles.
- **Headline Small (16px / Semibold):** Inner widget headers, sub-section dividers, and modal titles.
- **Body Default (14px / Regular):** The operational standard for tabular content, grid payloads, and descriptions.
- **Label & Caption (12-13px / Medium & Regular):** Key-value descriptive titles, breadcrumbs, timestamp tags, and secondary parameter units.

## Layout & Spacing

The system implements a fluid administrative layout pinned against a static collapsible master navigation drawer.

### Structure & Layout Grids
- **Navigation Shelf:** Fixed desktop width of 260px (`#0F172A`), collapsible to an icon rail of 64px on smaller monitors.
- **Work Area Canvas:** Fluid grid with fixed maximum width scaling up to 1920px before letterboxing with balanced canvas margins.
- **Grid Subdivision:** 12-column variable grid for device status dashboards; 1-column responsive stacked table for deployment pipelines.

### Breakpoints & Adaptive Rules
- **Desktop Large (≥ 1440px):** 24px content margins, 24px card gutters, persistent sidebar rail and side-by-side terminal inspector panels.
- **Desktop Standard (1024px – 1439px):** 24px margins, 16px gutters, collapsed telemetry panels.
- **Tablet / Industrial Field Tablet (768px – 1023px):** 16px margins, 16px gutters; sidebar converts to an off-canvas drawer. Device cards transition to 2 columns.
- **Mobile Handheld (< 768px):** 12px margins, 12px gutters; device lists switch from multi-column data tables to vertical stacked card lists.

## Elevation & Depth

Visual hierarchy uses flat structural containment rather than decorative elevation. Layer separation is established via 1px solid borders (`#E4E8EE`) complemented by extremely restrained, functional drop shadows.

### Elevation Levels
- **Level 0 (Canvas Base):** Ground layer (`#F4F6F9`), no border, no shadow.
- **Level 1 (Surface Containers & Cards):** `#FFFFFF` fill with `1px solid #E4E8EE`. Default state utilizes a subtle shadow: `0px 1px 2px rgba(15, 23, 42, 0.04)`.
- **Level 2 (Hover & Raised Elements):** Applied when hovering interactive cards or when dragging layout components. Shadow: `0px 4px 6px -1px rgba(15, 23, 42, 0.08), 0px 2px 4px -2px rgba(15, 23, 42, 0.04)`.
- **Level 3 (Overlays, Dropdowns & Command Menus):** `#FFFFFF` fill with `1px solid #E4E8EE`. Shadow: `0px 10px 15px -3px rgba(15, 23, 42, 0.08), 0px 4px 6px -4px rgba(15, 23, 42, 0.03)`.
- **Level 4 (System Modals & Drawers):** High-priority confirmation sheets and device diagnostics modals. Accompanied by a 40% `#0F172A` scrim with no blur effect.

## Shapes

The geometry reflects a balanced enterprise form factor with distinct structural thresholds:

- **Cards & Data Containers:** Standardized at `16px` (`rounded-lg` under roundedness level 2) to maintain a modern, friendly enterprise appearance while accommodating clean interior border alignments.
- **Inputs, Buttons & Form Elements:** Standardized at `8px` (`0.5rem`) for compact structural integrity and crisp click targets.
- **Status Badges & Pills:** Full pill contour (`9999px` / `rounded-full`) to distinguish functional telemetry tags from interactive rectangular buttons.
- **Segmented Controllers & Button Groups:** Encapsulated within an outer 8px boundary with inner items matching the nested geometry.

## Components

### Buttons
- **Primary:** `#2563EB` solid fill, white text, 8px radius, height 36px (dense) or 40px (default). Hover: `#1D4ED8`. Active: `#1E40AF`.
- **Secondary / Outline:** `#FFFFFF` fill, 1px solid `#E4E8EE`, text `#0F172A`. Hover: `#F4F6F9` background, border `#D0D7E2`.
- **Ghost / Action:** Transparent background, text `#5B6676`. Hover: `#F4F6F9`, text `#0F172A`.
- **Destructive:** `#FFFFFF` fill with 1px solid `#F8B4B4`, text `#B91C1C`. Hover: `#FDECEC`.

### Form Controls & Inputs
- **Text Inputs & Selects:** Height 38px, background `#FFFFFF`, border `1px solid #E4E8EE`, radius 8px, text `#0F172A`, placeholder `#5B6676`. Focus state applies a crisp `0 0 0 2px #2563EB` ring without glowing blurs.
- **Checkboxes & Radios:** 16x16px boundary, 4px corner radius for checkboxes, full circle for radios. Active checkmark filled with `#2563EB`.

### Status Indicators & Chips
- Status chips must always contain a leading 6px circular dot or an explicit 14px vector icon (e.g., check-circle, alert-triangle, slash-circle).
- Height: 24px, inner horizontal padding 8px, typography `12px` medium.
- Colors follow the semantic tokens: Online (`#E8F5EC` / `#15803D`), Degraded (`#FEF3E2` / `#B45309`), Offline (`#FDECEC` / `#B91C1C`).

### Cards & Container Panels
- Base containers use `#FFFFFF` fill, `1px solid #E4E8EE` border, and a 16px corner radius.
- Padding follows strict operational tiers: `16px` for high-density telemetry widgets and `24px` for configuration surfaces.
- Card headers feature an explicit 1px bottom divider `#E4E8EE` when housing tabular data.

### Signage-Specific Modules
- **Display Status Tile:** Displays screen name, IP address, orientation badge (Landscape/Portrait), current playlist thumbnail (16:9 aspect ratio, 8px radius, 1px border), and ping latency counter.
- **Playlist Timeline Bar:** Linear horizontal track showing content slots with 4px separation, displaying duration in seconds and format type badges.