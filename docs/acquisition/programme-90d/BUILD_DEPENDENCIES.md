# Frontend dependency prerequisite

The first programme candidate reached CI on 7 October 2026 and the existing
`npm audit --audit-level=high` gate failed before frontend tests. Its dependency
chain included the newly reviewed unpatched `braces <=3.0.3` denial-of-service
advisory (GHSA-vfj7-8cjw-p6xm), carried by Tailwind 3's glob/watch dependencies,
and `source-map-js <=1.2.1` (GHSA-68fv-2mgg-jv7q).

The original candidate repaired this chain using Tailwind 4.3.3 and PostCSS.
PR #56 subsequently released a separate Tailwind repair. On 14 October the
programme candidate was integrated with that production base and now preserves
its Vite plugin, exact lockfile, compatibility CSS and current UI components.
It does not replace the released build with a second dependency migration.
No security gate is bypassed. The browser floor remains Safari 16.4+, Chrome
111+ and Firefox 128+, already introduced by the released maintenance change.
Current candidate checks still precede any programme release.

Primary references:
- https://github.com/advisories/GHSA-vfj7-8cjw-p6xm
- https://github.com/advisories/GHSA-68fv-2mgg-jv7q
- https://tailwindcss.com/docs/upgrade-guide
