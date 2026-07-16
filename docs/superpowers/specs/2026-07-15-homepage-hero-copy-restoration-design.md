# Homepage Hero Copy Restoration

## Context

The Release 1 homepage replaced AutoSafe's established hero copy with methodology-led wording. The owner has explicitly approved restoring the historical brand copy exactly.

## Decision

Restore this exact pair on both homepage routes (`/` and `/app`):

- Headline: `Fix it before they find it.`
- Supporting line: `Taking the stress out of MOTs and repairs.`

## Scope

The implementation is limited to:

- Updating the two hero strings in `App.tsx`.
- Updating the existing home-page rendering test in `App.test.tsx` so both routes require both restored strings.

The change will not alter report copy, evidence semantics, API behaviour, metadata, analytics, styling, layout, or backend code. It will not deploy or push production state.

## Testing

Use the existing parameterised test for `/` and `/app` as the regression boundary:

1. Change the test expectation first and run it to observe the expected failure against the current copy.
2. Restore the two strings in `App.tsx`.
3. Run the focused test and the full frontend test suite.
4. Run the production frontend build.

## Success Criteria

- Both `/` and `/app` render the exact approved headline and supporting line.
- The previous methodology-led hero strings are no longer rendered.
- All frontend tests pass.
- The production frontend build succeeds.
- The diff contains no unrelated changes.
