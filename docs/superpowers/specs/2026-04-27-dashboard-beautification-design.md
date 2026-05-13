# Dashboard Beautification Design

**Date:** 2026-04-27
**Scope:** Visual redesign of AgentsPage (persona cards) and SkillsPage (book cards) using pure CSS abstract style
**Approach:** Frontend-only changes to `dashboard/static/app.js` and `dashboard/static/style.css`

---

## 1. Goals

- Transform agent cards into **RPG persona cards** with emoji avatars, titles, and attribute bars
- Transform skill cards into **book cards** with spine colors, paper texture, and bookmark tags
- Add icons to Overview summary cards
- Zero backend changes, zero image assets, pure CSS + emoji

---

## 2. Agent Persona Card Design

### Visual Elements

| Element | Implementation |
|---------|---------------|
| Avatar | Circular avatar area with emoji based on `agent.name` hash |
| Title | Auto-generated persona title based on `tools.length` |
| Layout | Left: large circular avatar; Right: name + description |
| Stats Bar | `linkedSkills.length` shown as colored progress bar at bottom |
| Border | Subtle colored border matching avatar personality |

### Emoji Assignment (name hash → emoji)

Pool: `['🤖', '🕵️', '🧙‍♂️', '👨‍💻', '👩‍🔬', '🦸', '🧑‍🚀', '🧑‍⚕️', '🧑‍🌾', '🧑‍🔬']`

Selection: `hash(name) % pool.length`

### Title Assignment (tools count)

| Tools Count | Title |
|-------------|-------|
| 0 | 见习者 |
| 1 | 学徒 |
| 2-3 | 工匠 |
| 4+ | 大师 |

### Color Assignment (name hash)

Border/avatar ring colors derived from name hash:
- `hsl(hash % 360, 70%, 60%)`

---

## 3. Skill Book Card Design

### Visual Elements

| Element | Implementation |
|---------|---------------|
| Spine | 6px left border with color based on `skill.name` hash |
| Cover | Slightly off-white background (`#faf8f5`) simulating paper |
| Icon | 📖 emoji prefix on title |
| Title | Larger font, slightly serif feel |
| Description | 3-line max with ellipsis |
| Bookmarks | Triggers shown as small rounded tags with fold effect |
| Hover | `translateY(-4px) rotateX(2deg)` lift effect |

### Spine Color (name hash)

`hsl(hash % 360, 65%, 55%)` — same hash function as agents but different saturation/lightness

---

## 4. Overview Summary Cards

Add emoji icons to existing 4 cards:

| Card | Emoji |
|------|-------|
| Events | 📊 |
| Active Jobs | ⏰ |
| Agents | 🧑‍🚀 |
| Skills | 📚 |

---

## 5. Files to Modify

- `dashboard/static/app.js` — Update `AgentsPage`, `SkillsPage`, `OverviewPage` templates
- `dashboard/static/style.css` — Add new CSS classes for persona cards, book cards, icons

## 6. No-Go

- No backend API changes
- No new data fields required
- No image assets
- No new routes
