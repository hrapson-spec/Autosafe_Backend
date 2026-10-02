/**
 * Neutralises public homepage metadata inherited from the HTML shell while a
 * bearer-token route (the report screen) is mounted.
 *
 * Every SPA route is served the same index.html. For `/app/report/*` the
 * server already strips the homepage canonical, description, Open Graph /
 * Twitter tags and WebSite/Organization JSON-LD (report_protection.py), but
 * a client-side transition home -> report reuses the document that was
 * loaded for the homepage, so those tags are still in <head>. This detaches
 * them for the lifetime of the screen and puts each back exactly where it
 * was on unmount, so report -> home leaves the homepage metadata intact.
 *
 * Tags owned by react-helmet-async (`data-rh`) are left alone: Helmet
 * removes its own tags when the page that rendered them unmounts, and
 * detaching them here would race its bookkeeping.
 */

const SHELL_METADATA_SELECTOR = [
  'link[rel~="canonical"]',
  'script[type="application/ld+json"]',
  'meta[name="description"]',
  'meta[property^="og:"]',
  'meta[name^="twitter:"]',
]
  .map((selector) => `head ${selector}:not([data-rh])`)
  .join(', ');

interface DetachedNode {
  node: Element;
  parent: Node;
  nextSibling: Node | null;
}

/** Detaches the inherited homepage metadata; returns a function that
 * restores it. Safe to call more than once (React StrictMode re-runs
 * effects): each call only touches what is currently attached. */
export function suppressShellMetadata(doc: Document = document): () => void {
  const detached: DetachedNode[] = [];
  doc.querySelectorAll(SHELL_METADATA_SELECTOR).forEach((node) => {
    const parent = node.parentNode;
    if (!parent) return;
    detached.push({ node, parent, nextSibling: node.nextSibling });
    parent.removeChild(node);
  });

  return () => {
    // Reverse order so each recorded nextSibling is already back in place
    // when its predecessor is re-inserted.
    for (let i = detached.length - 1; i >= 0; i -= 1) {
      const { node, parent, nextSibling } = detached[i];
      if (node.parentNode) continue;
      const anchor = nextSibling && nextSibling.parentNode === parent ? nextSibling : null;
      parent.insertBefore(node, anchor);
    }
  };
}
