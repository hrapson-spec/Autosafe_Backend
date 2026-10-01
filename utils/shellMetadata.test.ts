import { afterEach, describe, expect, it } from 'vitest';
import { suppressShellMetadata } from './shellMetadata';

afterEach(() => {
  document.head.innerHTML = '';
});

describe('suppressShellMetadata', () => {
  it('detaches homepage metadata, keeps everything else, and restores order exactly', () => {
    document.head.innerHTML = `
      <meta charset="UTF-8" />
      <meta name="description" content="d" />
      <meta property="og:title" content="t" />
      <meta name="twitter:card" content="c" />
      <link rel="canonical" href="https://www.autosafe.one/" />
      <link rel="icon" href="/f.png" />
      <script type="application/ld+json">{}</script>
      <script>var x = 1;</script>
      <meta name="viewport" content="width=device-width" />`;
    const before = document.head.innerHTML;

    const restore = suppressShellMetadata();
    expect(document.head.querySelectorAll('meta[name="description"], meta[property^="og:"], meta[name^="twitter:"], link[rel~="canonical"], script[type="application/ld+json"]')).toHaveLength(0);
    expect(document.head.querySelector('link[rel="icon"]')).not.toBeNull();
    expect(document.head.querySelector('meta[name="viewport"]')).not.toBeNull();
    expect(document.head.querySelectorAll('script')).toHaveLength(1);

    restore();
    expect(document.head.innerHTML).toBe(before);
  });

  it('ignores react-helmet-async managed tags (data-rh)', () => {
    document.head.innerHTML =
      '<meta name="description" content="helmet" data-rh="true" /><link rel="canonical" href="/x" data-rh="true" />';
    const restore = suppressShellMetadata();
    expect(document.head.querySelectorAll('[data-rh]')).toHaveLength(2);
    restore();
    expect(document.head.querySelectorAll('[data-rh]')).toHaveLength(2);
  });

  it('is a no-op on an empty head and restore is idempotent', () => {
    const restore = suppressShellMetadata();
    restore();
    restore();
    expect(document.head.children).toHaveLength(0);
  });

  it('restore does not duplicate a node that something else already re-attached', () => {
    document.head.innerHTML = '<link rel="canonical" href="/x" />';
    const node = document.head.firstElementChild as Element;
    const restore = suppressShellMetadata();
    document.head.appendChild(node);
    restore();
    expect(document.head.querySelectorAll('link[rel="canonical"]')).toHaveLength(1);
  });
});
