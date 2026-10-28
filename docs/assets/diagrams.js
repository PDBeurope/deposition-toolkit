// Keep Markdown fences readable in GitHub; render them on the Pages site.
const blocks = [...document.querySelectorAll('pre > code.language-mermaid, pre > code[data-lang="mermaid"], pre.language-mermaid > code, .language-mermaid pre > code')];
if (blocks.length) {
  try {
    const { default: mermaid } = await import('https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.esm.min.mjs');
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral' });
    // Render independently so an invalid diagram does not hide the other diagrams.
    for (const [index, block] of blocks.entries()) {
      try {
        const { svg } = await mermaid.render(`diagram-${index}`, block.textContent);
        const figure = document.createElement('figure');
        figure.className = 'diagram';
        figure.setAttribute('aria-label', 'Workflow diagram');
        figure.innerHTML = svg;
        block.closest('pre').replaceWith(figure);
      } catch (error) {
        console.error('Diagram rendering failed; source preserved.', error);
      }
    }
  } catch (error) {
    console.error('Mermaid unavailable; diagram source preserved.', error);
  }
}
