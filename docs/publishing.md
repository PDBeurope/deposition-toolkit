---
title: Documentation maintenance and GitHub Pages
---

[Documentation home](index.md) · [Developer guide](development.md)

# Documentation maintenance and GitHub Pages

## One source, two readers

These pages use ordinary relative Markdown links and fenced Mermaid diagrams. GitHub renders the Markdown directly. For GitHub Pages, `docs/_config.yml` selects a local Jekyll layout and `jekyll-relative-links` converts document links to generated page URLs. Page front matter supplies titles; no application Python packages or credentials are needed to build the site.

The layout uses a small stylesheet and `assets/diagrams.js` to turn Mermaid code fences into SVGs. Mermaid `11.12.0` is loaded from jsDelivr at viewing time, with strict security mode. If the CDN or JavaScript is unavailable, diagram source remains readable. This site is therefore not fully offline for diagram rendering.

This setup follows [GitHub's Jekyll documentation](https://docs.github.com/en/pages/setting-up-a-github-pages-site-with-jekyll/about-github-pages-and-jekyll) and the [Mermaid browser integration](https://mermaid.js.org/intro/getting-started.html).

## Enable publication

The repository settings are not changed by adding these files. After merging documentation:

1. In repository **Settings → Pages**, choose **Deploy from a branch**.
2. Select the branch containing these files, choose **`/docs`**, and save.
3. Check the Pages build/deployment result and follow the URL GitHub provides.

`_config.yml` assumes the repository is served under `/deposition-toolkit` on `https://pdbeurope.github.io`. Adjust `url` and `baseurl` if publishing under another repository name or custom domain. Keep the base path out of hand-written document links; use relative Markdown links. Deployment from a branch is sufficient for this site; an organization may instead choose a GitHub Actions build.

## Preview locally

With a suitable Ruby/Bundler environment:

```bash
cd docs
bundle install
bundle exec jekyll serve --baseurl ""
```

Open the local address Jekyll prints. `Gemfile` uses GitHub Pages' dependency set for compatible plugins. Installing these documentation dependencies is separate from `uv sync`. Do not commit generated `_site/`, `.jekyll-cache/`, `.bundle/` or `vendor/` content.

## Keep the docs aligned with code

- Parser changes: update [CLI reference](cli.md), examples and command internals.
- Environment/default changes: update [configuration](configuration.md) and `.env.example`.
- Discovery, identity or manifest changes: update [data and state](data-and-state.md).
- Layer/dependency changes: update [architecture](architecture.md), especially the MySQL exception.
- Algorithm/failure changes: revise the affected command's diagram and decision rules, then [recovery](recovery.md).

Use paths and function names to identify implementation locations; avoid brittle source line references. Keep diagrams focused on branches readers need to reason about. For more complex algorithms, split diagrams by responsibility rather than expanding one pipeline indefinitely.

## Check documentation

From the repository root:

```bash
uv run python docs/check_docs.py
```

The checker validates local Markdown file/fragment links, balanced fences, required page front matter, command-page coverage, and example JSON/YAML syntax. It does not build Jekyll, validate Mermaid grammar, or contact external links. Also run a Jekyll build and visually review Mermaid rendering when changing site templates or diagrams.
