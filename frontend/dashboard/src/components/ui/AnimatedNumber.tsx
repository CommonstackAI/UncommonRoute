import { useEffect, useRef } from "react";
import { useInView, useMotionValue, animate } from "framer-motion";

interface AnimatedNumberProps {
  value: number;
  format?: (val: number) => string;
  className?: string;
}

export function AnimatedNumber({
  value,
  format = (val) => val.toLocaleString(),
  className,
}: AnimatedNumberProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const motionValue = useMotionValue(0);
  const isInView = useInView(ref, { once: true, margin: "-20px" });

  useEffect(() => {
    if (ref.current) {
      ref.current.textContent = format(motionValue.get());
    }
  }, [motionValue, format]);

  useEffect(() => {
    if (isInView) {
      const controls = animate(motionValue, value, {
        duration: 0.8,
        ease: "easeOut",
      });
      return () => controls.stop();
    }
  }, [motionValue, isInView, value]);

  useEffect(() => {
    return motionValue.on("change", (latest) => {
      if (ref.current) {
        ref.current.textContent = format(latest);
      }
    });
  }, [motionValue, format]);

  return <span ref={ref} className={className} />;
}
