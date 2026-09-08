import MarkdownIt from "markdown-it";

const renderer = new MarkdownIt({
  html: false, // escape raw HTML: planner output is model-generated text
  linkify: true,
  breaks: true,
});

export function renderMarkdown(text: string | null | undefined): string {
  return renderer.render(text || "");
}
