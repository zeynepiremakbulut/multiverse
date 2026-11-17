import React, { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import { CodeBlock } from './CodeBlock';

interface MarkdownRendererProps {
  content: string;
}

/**
 * Sanitizes the inner content of a single LaTeX math block ($...$ or $$...$$):
 * - Fixes invalid math commands inside \text{...} like \text{\mu A} -> \mu\text{A} and \text{\Omega} -> \Omega
 * - Escapes raw unescaped % inside math (which comments out lines in LaTeX)
 * - Removes accidental internal dollar signs
 * - Normalizes single-backslash row breaks in matrices/aligned blocks to \\
 * - Auto-balances unclosed curly braces (e.g. unclosed \boxed{ or \frac{...)
 */
function sanitizeMathInner(raw: string): string {
  let m = raw;

  // 1. Fix \mu and \Omega inside \text{...}
  m = m.replace(/\\text\{([^{}]*?)\\mu([^{}]*?)\}/g, (_, before, after) => {
    return (before ? `\\text{${before}}` : '') + '\\mu ' + (after ? `\\text{${after}}` : '');
  });
  m = m.replace(/\\text\{([^{}]*?)\\Omega([^{}]*?)\}/g, (_, before, after) => {
    return (before ? `\\text{${before}}` : '') + '\\Omega ' + (after ? `\\text{${after}}` : '');
  });

  // 2. Fix unescaped % inside math
  m = m.replace(/(?<!\\)%/g, '\\%');

  // 3. Remove stray internal $ signs inside math
  m = m.replace(/\$/g, '');

  // 4. Fix single backslash row breaks in matrix / aligned
  m = m.replace(
    /(\\begin\{(?:pmatrix|matrix|bmatrix|vmatrix|Vmatrix|aligned)\}[\s\S]*?\\end\{(?:pmatrix|matrix|bmatrix|vmatrix|Vmatrix|aligned)\})/g,
    (match) => match.replace(/(?<!\\)\\\s+(?=\\frac|[-0-9a-zA-Z\\])/g, '\\\\ ')
  );

  // 5. Fix unbalanced curly braces (e.g. unclosed \boxed{ or \frac{...})
  const openBraces = (m.match(/\{/g) || []).length;
  const closeBraces = (m.match(/\}/g) || []).length;
  if (openBraces > closeBraces) {
    m = m + '}'.repeat(openBraces - closeBraces);
  }

  return m;
}

/**
 * Preprocesses LaTeX equations and formatting produced by LLMs to ensure standard KaTeX parsing:
 * - Protects code blocks from regex modifications
 * - Normalizes \[ ... \] to $$ ... $$ (display math)
 * - Normalizes \( ... \) to $ ... $ (inline math)
 * - Converts raw bracketed matrix environments [ U = \begin{pmatrix} ... \end{pmatrix} ] to $$ ... $$
 * - Wraps naked LaTeX environments in $$ ... $$
 * - Sanitizes all math blocks (fixing invalid \text{\mu}, unclosed \boxed{, etc.)
 * - Protects math blocks before safely converting naked prose symbols like \to, \Omega outside math
 * - Auto-closes dangling unclosed dollar signs from truncated responses
 */
function preprocessLaTeX(content: string): string {
  if (!content) return '';

  let text = content;

  // 1. Protect code blocks from being modified
  const codeBlocks: Array<{ placeholder: string; match: string }> = [];
  text = text.replace(/(```[\s\S]*?```|`[^`\n]+?`)/g, (match) => {
    const placeholder = `__CODE_BLOCK_${codeBlocks.length}__`;
    codeBlocks.push({ placeholder, match });
    return placeholder;
  });

  // 2. Standardize LaTeX display math \[ ... \] to $$ ... $$
  text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `\n\n$$\n${math.trim()}\n$$\n\n`);

  // 3. Standardize LaTeX inline math \( ... \) to $ ... $
  text = text.replace(/\\\(([\s\S]*?)\\\)/g, (_, math) => `$${math.trim()}$`);

  // 4. Convert bracketed math [ U = \begin{pmatrix} ... \end{pmatrix} ] to $$
  text = text.replace(
    /(?:^|(?<=[^\w\\]))\[\s*([^[\]\n]*?(?:\\begin|\\frac|\\sqrt|\\pmatrix|\\matrix|\\bmatrix|\\vmatrix|\\quad|\\alpha|\\beta|\\gamma|\\lambda|\\theta|\\pi|\\sum|\\int|\\partial|\\cdot|\\times|\\pm|\\mp|\\in|\\subset|\\mathbf|\\mathbb|\\det|\\sin|\\cos|\\tan)[\s\S]*?)\s*\](?!\()/g,
    (_, math) => `\n\n$$\n${math.trim()}\n$$\n\n`
  );

  // 5. Wrap naked \begin{matrix/pmatrix/cases/aligned...} in $$
  text = text.replace(
    /(?<!\$\$[\s\S]*?)(\\begin\{(?:pmatrix|matrix|bmatrix|vmatrix|Vmatrix|cases|aligned|align|gather)\}[\s\S]*?\\end\{(?:pmatrix|matrix|bmatrix|vmatrix|Vmatrix|cases|aligned|align|gather)\})/g,
    (match) => `\n\n$$\n${match.trim()}\n$$\n\n`
  );

  // 6. Sanitize existing $$ ... $$ display blocks
  text = text.replace(/\$\$([\s\S]*?)\$\$/g, (_, block) => {
    return `\n\n$$\n${sanitizeMathInner(block.trim())}\n$$\n\n`;
  });

  // 7. Sanitize existing $ ... $ inline blocks
  text = text.replace(/(?<!\$)\$([^\$\n]+?)\$(?!\$)/g, (_, inline) => {
    return `$${sanitizeMathInner(inline.trim())}$`;
  });

  // 8. Outside of math blocks, protect math blocks before fixing prose symbols
  const mathBlocks: Array<{ placeholder: string; match: string }> = [];
  text = text.replace(/(\$\$[\s\S]*?\$\$|\$[^\$\n]+?\$)/g, (match) => {
    const placeholder = `__MATH_BLOCK_${mathBlocks.length}__`;
    mathBlocks.push({ placeholder, match });
    return placeholder;
  });

  // Safely fix symbols in prose (arrows, Ohm, micro)
  text = text.replace(/\\(?:to|rightarrow)\b/g, ' $\\to$ ');
  text = text.replace(/([0-9.]+)?\s*(?:k|M|m)?\\Omega\b/g, (match) => ` $${match.trim()}$ `);
  text = text.replace(/\\(?:mu|micro)\s*(A|F|V|s|m)?\b/g, (_, unit) => ` $\\mu${unit ? `\\text{${unit}}` : ''}$ `);
  text = text.replace(/\?=(I[0-9a-zA-Z_]+|V[0-9a-zA-Z_]+)/g, '($1 = ?)');

  // Restore math blocks
  mathBlocks.forEach(({ placeholder, match }) => {
    text = text.replace(placeholder, match);
  });

  // Restore code blocks
  codeBlocks.forEach(({ placeholder, match }) => {
    text = text.replace(placeholder, match);
  });

  // 9. Auto-close dangling unclosed single dollars
  const lines = text.split('\n');
  const fixedLines = lines.map((line) => {
    if (line.includes('$$')) return line;
    const matches = line.match(/(?<!\\)\$/g);
    if (matches && matches.length % 2 !== 0) {
      return line.trimEnd() + '$';
    }
    return line;
  });
  text = fixedLines.join('\n');

  const globalDollarMatches = text.match(/(?<!\\)\$/g);
  if (globalDollarMatches && globalDollarMatches.length % 2 !== 0) {
    text = text.trimEnd() + '$';
  }

  // 10. Fix dangling unclosed markdown brackets or bold markers
  const openBrackets = (text.match(/\[/g) || []).length;
  const closeBrackets = (text.match(/\]/g) || []).length;
  if (openBrackets > closeBrackets) {
    text += ']';
  }
  const boldMatches = text.match(/\*\*/g);
  if (boldMatches && boldMatches.length % 2 !== 0) {
    text += '**';
  }

  return text;
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content }) => {
  const normalizedContent = useMemo(() => preprocessLaTeX(content), [content]);

  return (
    <div className="prose prose-invert prose-sm max-w-none text-slate-200 leading-relaxed space-y-2.5">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: true }]]}
        rehypePlugins={[rehypeRaw, [rehypeKatex, { output: 'htmlAndMathml', throwOnError: false }]]}
        components={{
          video({ node, ...props }: any) {
            return (
              <div className="my-3 rounded-xl overflow-hidden border border-slate-800 bg-black/80">
                <video
                  {...props}
                  controls
                  playsInline
                  className="w-full max-h-[440px] object-cover mx-auto"
                />
              </div>
            );
          },
          code({ inline, className, children, ...props }: any) {
            const match = /language-(\w+)/.exec(className || '');
            const value = String(children).replace(/\n$/, '');
            if (!inline && (match || value.includes('\n'))) {
              return <CodeBlock language={match ? match[1] : undefined} value={value} />;
            }
            return (
              <code
                className="bg-space-850 text-cyan-300 font-mono text-[12px] px-1.5 py-0.5 rounded border border-slate-800"
                {...props}
              >
                {children}
              </code>
            );
          },
          p({ children }) {
            return <p className="mb-2 last:mb-0 text-slate-200 text-sm leading-relaxed">{children}</p>;
          },
          h1({ children }) {
            return <h1 className="text-lg font-bold text-slate-100 mt-4 mb-2 border-b border-slate-800 pb-1">{children}</h1>;
          },
          h2({ children }) {
            return <h2 className="text-base font-semibold text-cyan-300 mt-3.5 mb-1.5">{children}</h2>;
          },
          h3({ children }) {
            return <h3 className="text-sm font-semibold text-slate-100 mt-2.5 mb-1">{children}</h3>;
          },
          ul({ children }) {
            return <ul className="list-disc list-outside pl-5 mb-2.5 space-y-1.5 text-slate-200 text-sm">{children}</ul>;
          },
          ol({ children }) {
            return <ol className="list-decimal list-outside pl-5 mb-2.5 space-y-1.5 text-slate-200 text-sm">{children}</ol>;
          },
          li({ children }) {
            return <li className="text-slate-200 leading-relaxed pl-1">{children}</li>;
          },
          blockquote({ children }) {
            return (
              <blockquote className="border-l-3 border-cyan-500/80 pl-3.5 italic text-slate-300 my-2.5 text-sm bg-space-900/60 py-1.5 rounded-r">
                {children}
              </blockquote>
            );
          },
          table({ children }) {
            return (
              <div className="overflow-x-auto my-3 rounded-xl border border-slate-700/60 shadow-sm">
                <table className="min-w-full divide-y divide-slate-700/60 text-xs text-left">
                  {children}
                </table>
              </div>
            );
          },
          th({ children }) {
            return <th className="px-3.5 py-2.5 bg-space-850 font-semibold text-slate-200">{children}</th>;
          },
          td({ children }) {
            return <td className="px-3.5 py-2 border-t border-slate-800/80 text-slate-300">{children}</td>;
          },
          a({ href, children }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-cyan-400 hover:text-cyan-300 underline underline-offset-2 transition-colors"
              >
                {children}
              </a>
            );
          }
        }}
      >
        {normalizedContent}
      </ReactMarkdown>
    </div>
  );
};
