import ReactMarkdown from "react-markdown";

interface Props {
  text: string;
}

export default function MarkdownContent({ text }: Props) {
  return (
    <div className="prose-chat text-[14px] leading-relaxed text-n-primary">
      <ReactMarkdown
        disallowedElements={["html", "img"]}
        unwrapDisallowed
        components={{
          code({ className, children, ...rest }) {
            const isBlock = /language-/.test(className ?? "");
            if (isBlock) {
              return (
                <pre className="my-2 overflow-x-auto rounded-compact border border-n-border bg-n-raised px-3 py-2 font-mono text-[12px] text-n-primary">
                  <code {...rest}>{children}</code>
                </pre>
              );
            }
            return (
              <code className="rounded bg-n-raised px-1 py-0.5 font-mono text-[12px] text-n-display">
                {children}
              </code>
            );
          },
          a({ children, href }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-n-accent underline"
              >
                {children}
              </a>
            );
          },
          ul({ children }) {
            return <ul className="my-1 ml-5 list-disc">{children}</ul>;
          },
          ol({ children }) {
            return <ol className="my-1 ml-5 list-decimal">{children}</ol>;
          },
          h1({ children }) {
            return <h1 className="mt-3 mb-1 text-[16px] font-semibold text-n-display">{children}</h1>;
          },
          h2({ children }) {
            return <h2 className="mt-3 mb-1 text-[15px] font-semibold text-n-display">{children}</h2>;
          },
          h3({ children }) {
            return <h3 className="mt-2 mb-1 text-[14px] font-semibold text-n-display">{children}</h3>;
          },
          h4({ children }) {
            return <h4 className="mt-2 mb-1 text-[13px] font-semibold text-n-display">{children}</h4>;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
