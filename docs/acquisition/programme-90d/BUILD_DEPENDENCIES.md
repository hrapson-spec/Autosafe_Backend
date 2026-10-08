# Frontend dependency prerequisite

The first programme candidate reached CI on 7 October 2026 and the existing
`npm audit --audit-level=high` gate failed before frontend tests. Its dependency
chain included the newly reviewed unpatched `braces <=3.0.3` denial-of-service
advisory (GHSA-vfj7-8cjw-p6xm), carried by Tailwind 3's glob/watch dependencies,
and `source-map-js <=1.2.1` (GHSA-68fv-2mgg-jv7q).

The candidate removes that chain by migrating to Tailwind 4.3.3 and its dedicated
PostCSS plugin using the official upgrade tool. Source-map-js resolves to 1.2.2.
No security gate is bypassed. Utility names are migrated to preserve their
prior meaning, the custom fade-in animation moves to CSS, and default border
compatibility is retained. App wording is unchanged. The root lockfile remains
the reproducible install source. Audit after this change reports zero known
vulnerabilities; this is a current audit result, not a guarantee of security.

This introduces the Tailwind 4 browser floor: Safari 16.4+, Chrome 111+ and
Firefox 128+. Review this compatibility change before release. Server-rendered
model content keeps its existing CSS; the new explorer and tables have separate
mobile checks. The SPA receives complete frontend and browser regression checks,
including supported fresh reports, failed reports, consent and privacy routes.

Primary references:
- https://github.com/advisories/GHSA-vfj7-8cjw-p6xm
- https://github.com/advisories/GHSA-68fv-2mgg-jv7q
- https://tailwindcss.com/docs/upgrade-guide
