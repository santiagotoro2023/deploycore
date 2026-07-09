import { useEffect, useRef } from "react";

interface Point {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

const DOT_SPACING_PX = 75; // roughly one dot per this many square px
const MAX_LINK_DISTANCE = 150;
const SPEED = 0.12;

/** A quiet, slowly-drifting network of dots behind the sign-in/setup-wizard
 * cards, layered on top of the blurred color blobs for a touch more visual
 * interest without competing with the form itself. Respects
 * prefers-reduced-motion (renders one still frame instead of animating) and
 * reads light/dark straight off <html class="dark">, same signal the rest
 * of the app's theme switching already uses. */
export default function ConnectedDots({ opacity = 1 }: { opacity?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let points: Point[] = [];
    let width = 0;
    let height = 0;
    let animationFrame = 0;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function isDark() {
      return document.documentElement.classList.contains("dark");
    }

    function resize() {
      const parent = canvas!.parentElement;
      if (!parent) return;
      width = parent.clientWidth;
      height = parent.clientHeight;
      canvas!.width = width * window.devicePixelRatio;
      canvas!.height = height * window.devicePixelRatio;
      canvas!.style.width = `${width}px`;
      canvas!.style.height = `${height}px`;
      ctx!.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);

      const count = Math.min(70, Math.max(18, Math.round((width * height) / (DOT_SPACING_PX * DOT_SPACING_PX))));
      points = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * SPEED,
        vy: (Math.random() - 0.5) * SPEED,
      }));
    }

    function step() {
      ctx!.clearRect(0, 0, width, height);
      const dark = isDark();
      const dotColor = dark ? "rgba(224, 242, 254, 1)" : "rgba(23, 37, 84, 1)";
      const dotRadius = dark ? 3 : 3.4;
      const dotGlowColor = dark ? "rgba(125, 211, 252, 0.95)" : "rgba(37, 99, 235, 0.9)";
      const lineColor = dark ? "165, 213, 253" : "29, 78, 216";

      for (const p of points) {
        if (!reduceMotion) {
          p.x += p.vx;
          p.y += p.vy;
          if (p.x < 0 || p.x > width) p.vx *= -1;
          if (p.y < 0 || p.y > height) p.vy *= -1;
        }
      }

      for (let i = 0; i < points.length; i++) {
        for (let j = i + 1; j < points.length; j++) {
          const a = points[i];
          const b = points[j];
          const dist = Math.hypot(a.x - b.x, a.y - b.y);
          if (dist > MAX_LINK_DISTANCE) continue;
          const lineOpacity = (1 - dist / MAX_LINK_DISTANCE) * (dark ? 0.85 : 0.9) * opacity;
          ctx!.strokeStyle = `rgba(${lineColor}, ${lineOpacity})`;
          ctx!.lineWidth = 1.6;
          ctx!.beginPath();
          ctx!.moveTo(a.x, a.y);
          ctx!.lineTo(b.x, b.y);
          ctx!.stroke();
        }
      }

      ctx!.globalAlpha = opacity;
      ctx!.shadowColor = dotGlowColor;
      ctx!.shadowBlur = 6;
      for (const p of points) {
        ctx!.fillStyle = dotColor;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, dotRadius, 0, Math.PI * 2);
        ctx!.fill();
      }
      ctx!.shadowBlur = 0;
      ctx!.globalAlpha = 1;

      if (!reduceMotion) animationFrame = requestAnimationFrame(step);
    }

    resize();
    step();
    window.addEventListener("resize", resize);

    // A window resize is the only thing that changes a login/setup card's
    // size, but on a page like the Dashboard the container can grow taller
    // after mount too, once its async data finishes loading and renders
    // more content, well after this only measured the pre-data height once.
    // ResizeObserver catches that; window's own resize event doesn't.
    const parent = canvas.parentElement;
    const observer = parent ? new ResizeObserver(resize) : null;
    if (parent) observer!.observe(parent);

    return () => {
      window.removeEventListener("resize", resize);
      observer?.disconnect();
      cancelAnimationFrame(animationFrame);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <canvas ref={canvasRef} className="absolute inset-0" aria-hidden="true" />;
}
