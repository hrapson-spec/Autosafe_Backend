# Homepage Hero Copy Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore AutoSafe's exact historical homepage headline and supporting line on both `/` and `/app`.

**Architecture:** Keep the change inside the existing `HomePage` JSX in `App.tsx`; no new component, state, or data path is needed. Extend the existing parameterised route-rendering test in `App.test.tsx` so the approved copy is a regression-protected UI contract.

**Tech Stack:** React 19, TypeScript, React Router, Vitest, Testing Library, Vite.

## Global Constraints

- Headline must be exactly `Fix it before they find it.`
- Supporting line must be exactly `Taking the stress out of MOTs and repairs.`
- Both `/` and `/app` must render both strings.
- Do not alter report copy, evidence semantics, API behaviour, metadata, analytics, styling, layout, or backend code.
- Do not deploy or push production state.
- Keep the diff limited to `App.tsx`, `App.test.tsx`, and this already-approved documentation trail.

---

### Task 1: Restore and regression-protect the homepage hero copy

**Files:**
- Modify: `App.test.tsx:78-88`
- Modify: `App.tsx:83-91`
- Reference: `docs/superpowers/specs/2026-07-15-homepage-hero-copy-restoration-design.md`

**Interfaces:**
- Consumes: `App` rendered inside `MemoryRouter` by `renderApp(initialEntries: string[])`.
- Produces: the exact approved headline and supporting line on the existing `/` and `/app` routes.

- [ ] **Step 1: Change the existing route-rendering test first**

Replace the current headline-only assertion inside `it.each([['/'], ['/app']])` with the approved copy contract:

```tsx
expect(
  await screen.findByRole('heading', {
    level: 1,
    name: 'Fix it before they find it.',
  })
).toBeInTheDocument();
expect(screen.getByText('Taking the stress out of MOTs and repairs.')).toBeInTheDocument();
expect(screen.queryByText('See what the MOT evidence says.')).not.toBeInTheDocument();
```

Keep the existing form-field and button assertions immediately after these assertions.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
npm test -- App.test.tsx -t "renders the check form"
```

Expected: FAIL for both parameterised routes because the current `<h1>` still renders `See what the MOT evidence says.` and the restored headline cannot be found.

- [ ] **Step 3: Make the minimal production change**

In `App.tsx`, replace only the two hero text nodes:

```tsx
<h1 className="text-5xl md:text-7xl font-serif font-medium text-slate-900 tracking-tight leading-tight">
  Fix it before they find it.
</h1>

<p className="text-lg md:text-xl text-slate-500 font-light tracking-wide max-w-lg mx-auto font-sans">
  Taking the stress out of MOTs and repairs.
</p>
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
npm test -- App.test.tsx -t "renders the check form"
```

Expected: PASS for both `/` and `/app` cases.

- [ ] **Step 5: Run the complete App test file**

Run:

```bash
npm test -- App.test.tsx
```

Expected: exit code 0 with every `App.test.tsx` test passing.

- [ ] **Step 6: Run full frontend verification**

Run each command and require exit code 0:

```bash
npm test
npm run typecheck
npm run build
git diff --check
```

Expected: all Vitest tests pass, TypeScript reports no errors, Vite completes a production build, and Git reports no whitespace errors.

- [ ] **Step 7: Review scope and commit the implementation**

Run:

```bash
git status --short
git diff -- App.tsx App.test.tsx
```

Expected: only the approved hero-copy assertions and the two hero strings have changed in production/test code.

Commit:

```bash
git add App.tsx App.test.tsx
git commit -m "fix: restore homepage hero copy"
```

Do not push or deploy.
