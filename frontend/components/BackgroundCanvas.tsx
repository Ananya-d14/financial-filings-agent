"use client";

import { useEffect, useRef } from "react";

interface Shape {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  sides: number; // 3=triangle, 4=square, 6=hexagon, 0=circle
  rotation: number;
  rotSpeed: number;
  opacity: number;
  lineWidth: number;
  dashed: boolean;
}

export function BackgroundCanvas() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf: number;
    let shapes: Shape[] = [];

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resize();
    window.addEventListener("resize", resize);

    // Spawn shapes across the canvas
    const TYPES = [3, 4, 6, 0]; // triangle, square, hex, circle
    const COUNT = 28;

    for (let i = 0; i < COUNT; i++) {
      const size = 18 + Math.random() * 60;
      shapes.push({
        x: Math.random() * window.innerWidth,
        y: Math.random() * window.innerHeight,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        size,
        sides: TYPES[Math.floor(Math.random() * TYPES.length)],
        rotation: Math.random() * Math.PI * 2,
        rotSpeed: (Math.random() - 0.5) * 0.012,
        opacity: 0.07 + Math.random() * 0.13,
        lineWidth: Math.random() > 0.5 ? 1 : 0.5,
        dashed: Math.random() > 0.65,
      });
    }

    function polygon(n: number, r: number) {
      ctx!.beginPath();
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2 - Math.PI / 2;
        if (i === 0) ctx!.moveTo(Math.cos(a) * r, Math.sin(a) * r);
        else ctx!.lineTo(Math.cos(a) * r, Math.sin(a) * r);
      }
      ctx!.closePath();
    }

    function draw() {
      ctx!.clearRect(0, 0, canvas!.width, canvas!.height);

      for (const s of shapes) {
        // Drift
        s.x += s.vx;
        s.y += s.vy;
        s.rotation += s.rotSpeed;

        // Wrap around edges
        const pad = s.size + 20;
        if (s.x < -pad) s.x = canvas!.width + pad;
        else if (s.x > canvas!.width + pad) s.x = -pad;
        if (s.y < -pad) s.y = canvas!.height + pad;
        else if (s.y > canvas!.height + pad) s.y = -pad;

        ctx!.save();
        ctx!.translate(s.x, s.y);
        ctx!.rotate(s.rotation);
        ctx!.strokeStyle = `rgba(0, 212, 170, ${s.opacity})`;
        ctx!.lineWidth = s.lineWidth;
        if (s.dashed) ctx!.setLineDash([4, 6]);
        else ctx!.setLineDash([]);

        if (s.sides === 0) {
          // Circle
          ctx!.beginPath();
          ctx!.arc(0, 0, s.size / 2, 0, Math.PI * 2);
          ctx!.stroke();
        } else {
          polygon(s.sides, s.size / 2);
          ctx!.stroke();
        }

        // Some large shapes get a second inner ring for depth
        if (s.size > 50 && s.sides !== 0) {
          ctx!.setLineDash([2, 8]);
          ctx!.globalAlpha = 0.4;
          polygon(s.sides, s.size / 3.5);
          ctx!.stroke();
          ctx!.globalAlpha = 1;
        }

        ctx!.restore();
      }

      raf = requestAnimationFrame(draw);
    }

    draw();

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <canvas
      ref={ref}
      style={{
        position: "fixed",
        inset: 0,
        width: "100%",
        height: "100%",
        zIndex: 0,
        pointerEvents: "none",
      }}
    />
  );
}
