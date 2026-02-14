import { useState, useEffect, RefObject } from 'react';

interface UseStickyCtaVisibilityOptions {
  blockRef: RefObject<HTMLDivElement>;
  hasSubmitted: boolean;
}

export function useStickyCtaVisibility({ blockRef, hasSubmitted }: UseStickyCtaVisibilityOptions): { showStickyCta: boolean } {
  const [isBlockVisible, setIsBlockVisible] = useState(true);
  const [isMobile, setIsMobile] = useState(false);
  const [isKeyboardOpen, setIsKeyboardOpen] = useState(false);

  // Track whether recommendation block is in viewport
  useEffect(() => {
    const el = blockRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsBlockVisible(entry.isIntersecting);
      },
      { threshold: 0 }
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [blockRef]);

  // Track viewport width
  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 768);
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Detect keyboard open via visualViewport
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;

    const initialHeight = vv.height;
    const handleResize = () => {
      const shrink = 1 - vv.height / initialHeight;
      setIsKeyboardOpen(shrink > 0.3);
    };

    vv.addEventListener('resize', handleResize);
    return () => vv.removeEventListener('resize', handleResize);
  }, []);

  const showStickyCta = isMobile && !isBlockVisible && !hasSubmitted && !isKeyboardOpen;

  return { showStickyCta };
}
