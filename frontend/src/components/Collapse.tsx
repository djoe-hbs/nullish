import type { ReactNode } from "react";

/** Smooth height reveal (CSS grid-rows trick). Closed content is `inert`: not focusable,
 *  not read by screen readers, so hidden details stay out of the way until asked for. */
export default function Collapse({
  open,
  id,
  children,
}: {
  open: boolean;
  id?: string;
  children: ReactNode;
}) {
  const closed = open ? {} : ({ inert: "" } as object);
  return (
    <div id={id} className={`collapse${open ? " open" : ""}`} {...closed}>
      <div className="collapse-inner">{children}</div>
    </div>
  );
}
