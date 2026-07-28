# Figmint editor design

We want a web GUI that allows us to insert figures from a local directory
and keeps track of their provenance.
It will eventually save to a text-based format so AI agents can edit,
but we'll be able to see exactly where each component came from,
and know if one has changed in a way that makes our composite figure
stale.

We can use React Typescript with Vite for the front end.

Some inspiration we could take:

- tldraw
- draw.io
- Figrecipe

We want to be compatible with Stencila for rendering and provenance tracking.

A larger and more general problem to handle is the idea of embedding
components in others and not losing track of where they came from,
so they form a bit of a knowledge graph.

## Motivating use cases

AI agents can easily "deep fake" a multi-panel scientific figure,
with illustrations, charts that look like they were produced from
data, raw images from microscopes, etc.

Researchers can forget to reexport a composite figure after, e.g.,
updating data processing, leaving the published version out of date.
At the very least the friction and cognitive overhead associated with
keeping track of staleness should go away.
At the same time, some don't have the patience to script every figure,
and so need a WYSIWYG interface.
However, interactive editing can cause us to lose track of important
subcomponents with their own provenance concerns.

## Demo

1. Insert one figure, which is an illustration created by AI.
2. Insert another figure, which is created by a Python script from data.
3. User tells AI agent to make one of the figures bigger.
4. User tells AI agent to change color of markers, so it edits Python script
   and web UI updates automatically on change.
5. `figmint status` run by AI agent shows that the composite output is stale,
   so the agent runs `figmint build`.
6. Just as easily, the figmint UI can build/export the composite image and
   `figmint status` shows it's up-to-date, since none of the input components
   have changed.
