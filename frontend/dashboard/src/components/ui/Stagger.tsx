import { motion } from "framer-motion";
import { ReactNode } from "react";

const containerVariants = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: {
      staggerChildren: 0.05,
    },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { type: "spring" as const, stiffness: 300, damping: 24 } },
};

export function StaggerContainer({
  children,
  className,
  as: Component = motion.div,
}: {
  children: ReactNode;
  className?: string;
  as?: any;
}) {
  return (
    <Component
      variants={containerVariants}
      initial="hidden"
      animate="show"
      className={className}
    >
      {children}
    </Component>
  );
}

export function StaggerItem({
  children,
  className,
  as: Component = motion.div,
}: {
  children: ReactNode;
  className?: string;
  as?: any;
}) {
  return (
    <Component variants={itemVariants} className={className}>
      {children}
    </Component>
  );
}
