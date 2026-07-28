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
