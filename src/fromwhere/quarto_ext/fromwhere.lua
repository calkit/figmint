--[[
fromwhere.lua — the Quarto half of fromwhere's provenance panels.

Everything that decides *what to say* about an artifact lives in Python, in
`fromwhere/quarto.py`, shared with the MyST plugin and with `fromwhere status`.
This file does the one thing Python cannot do from outside the render: turn the
nodes that come back into Pandoc's AST so Quarto can number the figure, collapse
the callout, and draw the diagram.

It must run at `at: pre-ast`, before Quarto's own filters. Callouts, crossrefs
and figures are all built by those filters, so a panel emitted after them is a
plain div with a colour class and a figure nobody numbered.
]]

local json = pandoc.json

--- The `fromwhere-quarto` executable, resolved once and remembered.
--- `false` once every candidate has failed, so a document with twenty figures
--- does not probe twenty times over.
local command = nil
--- Set from `fromwhere: command:` in the document's metadata.
local configured = nil
--- Mermaid support is asked of Quarto at most once per render.
local diagrams_ready = false

local CALLOUT = {
  note = "callout-note",
  tip = "callout-tip",
  warning = "callout-warning",
  caution = "callout-caution",
  -- Quarto has no `danger`; `important` is the red one, which is what the
  -- panel means by it.
  danger = "callout-important",
  important = "callout-important",
}

local function warn(message)
  quarto.log.warning("fromwhere: " .. message)
end

-- --------------------------------------------------------------------------
-- Talking to fromwhere
-- --------------------------------------------------------------------------

local function candidates()
  local list = {}
  local override = os.getenv("FROMWHERE_QUARTO")
  if override then
    table.insert(list, override)
  end
  if configured then
    table.insert(list, configured)
  end
  table.insert(list, "fromwhere-quarto")
  -- The project's own environment, which is where it is installed in practice:
  -- fromwhere is a dependency of the document, not of the machine.
  table.insert(list, ".venv/bin/fromwhere-quarto")
  table.insert(list, ".venv/Scripts/fromwhere-quarto.exe")
  return list
end

--- Find the executable by asking each candidate to describe itself.
-- Probing with no arguments is unambiguous: it either prints the spec and
-- exits 0 or it is not the program we are looking for. Distinguishing that
-- from a real rendering failure by watching a directive call fail would leave
-- a broken document looking like a missing install.
local function resolve()
  if command ~= nil then
    return command
  end
  for _, candidate in ipairs(candidates()) do
    if pcall(pandoc.pipe, candidate, {}, "") then
      command = candidate
      return command
    end
  end
  command = false
  warn(
    "could not run `fromwhere-quarto`. Install fromwhere in the environment this "
      .. "document is built with (`uv add fromwhere`), or set "
      .. "FROMWHERE_QUARTO to its path."
  )
  return command
end

local function call(directive, payload)
  local program = resolve()
  if not program then
    return nil
  end
  local ok, out = pcall(
    pandoc.pipe,
    program,
    { "--directive", directive },
    json.encode(payload)
  )
  if not ok then
    warn("`" .. directive .. "` failed: " .. tostring(out))
    return nil
  end
  local decoded, result = pcall(json.decode, out)
  if not decoded then
    warn("`" .. directive .. "` returned something that is not JSON")
    return nil
  end
  return result
end

--- The directory the document lives in.
-- Quarto resolves a relative path against the file it appears in, so fromwhere
-- has to as well, or a document in a subdirectory would look up an artifact
-- that is not there.
local function document_directory()
  local input = quarto.doc.input_file
  if input and input ~= "" then
    return pandoc.path.directory(input)
  end
  return pandoc.system.get_working_directory()
end

-- --------------------------------------------------------------------------
-- Nodes to Pandoc
--
-- The vocabulary is small and closed: it is exactly what fromwhere's shared
-- renderer emits, and both halves are in this repository.
-- --------------------------------------------------------------------------

local inlines, blocks

--- Plain text as proper `Str`/`Space` inlines rather than one long `Str`.
local function words(value)
  local out = pandoc.Inlines({})
  local first = true
  for piece in string.gmatch(tostring(value) .. " ", "([^ ]*) ") do
    if not first then
      out:insert(pandoc.Space())
    end
    if piece ~= "" then
      out:insert(pandoc.Str(piece))
    end
    first = false
  end
  return out
end

local function inline(node)
  local kind = node.type
  if kind == "text" then
    return words(node.value or "")
  elseif kind == "strong" then
    return pandoc.Inlines({ pandoc.Strong(inlines(node.children)) })
  elseif kind == "emphasis" then
    return pandoc.Inlines({ pandoc.Emph(inlines(node.children)) })
  elseif kind == "inlineCode" then
    return pandoc.Inlines({ pandoc.Code(node.value or "") })
  elseif kind == "link" then
    return pandoc.Inlines({
      pandoc.Link(inlines(node.children), node.url or ""),
    })
  end
  warn("unknown inline node `" .. tostring(kind) .. "`")
  return pandoc.Inlines({})
end

inlines = function(nodes)
  local out = pandoc.Inlines({})
  for _, node in ipairs(nodes or {}) do
    out:extend(inline(node))
  end
  return out
end

local function image(node)
  local attributes = {}
  for _, key in ipairs({ "width", "height" }) do
    if node[key] then
      table.insert(attributes, { key, node[key] })
    end
  end
  if node.align then
    table.insert(attributes, { "fig-align", node.align })
  end
  local alt = node.alt and words(node.alt) or pandoc.Inlines({})
  return pandoc.Image(alt, node.url or "", "", pandoc.Attr("", {}, attributes))
end

local function data_table(node)
  local head, body, columns = {}, {}, 0
  for index, row in ipairs(node.children or {}) do
    local cells, header = {}, false
    for _, item in ipairs(row.children or {}) do
      if item.header then
        header = true
      end
      table.insert(cells, { pandoc.Plain(inlines(item.children)) })
    end
    if #cells > columns then
      columns = #cells
    end
    if index == 1 and header then
      head = cells
    else
      table.insert(body, cells)
    end
  end
  local aligns, widths = {}, {}
  for index = 1, columns do
    aligns[index] = pandoc.AlignDefault
    widths[index] = 0
  end
  return pandoc.utils.from_simple_table(
    pandoc.SimpleTable({}, aligns, widths, head, body)
  )
end

--- HTML-escaped, and parenthesized so `gsub`'s replacement count stays out of
--- the caller's expression.
local function escape(value)
  return (value:gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;"))
end

--- An interactive figure — a Plotly or Altair chart, a rendered notebook.
-- Framed rather than inlined so the chart's own scripts and styles cannot
-- reach the document around it. Raw HTML because there is no Pandoc element
-- for a frame; outside HTML the file is named instead, which is the honest
-- answer when the format cannot show a web page at all.
local function embed(node)
  local url = tostring(node.url or "")
  if not quarto.doc.is_format("html:js") then
    return pandoc.Blocks({
      pandoc.Para({
        pandoc.Str("Interactive figure: "),
        pandoc.Code(url),
        pandoc.Str(" (open it in a browser; this format cannot show it)."),
      }),
    })
  end
  -- A raw *inline* in a Plain, not a RawBlock. Quarto rebuilds a figure's
  -- content when it numbers it and discards raw blocks on the way through, so
  -- a RawBlock here renders as an empty box under a correct caption — which
  -- looks like a broken figure and reads like a missing one.
  return pandoc.Blocks({
    pandoc.Plain({
      pandoc.RawInline(
        "html",
        '<iframe src="'
          .. escape(url)
          .. '" style="width:'
          .. escape(tostring(node.width or "100%"))
          .. ";height:"
          .. escape(tostring(node.height or "420px"))
          .. ';border:none" loading="lazy"></iframe>'
      ),
    }),
  })
end

--- The derivation DAG, drawn where it can be drawn.
-- Quarto renders Mermaid in its *engine*, before pandoc ever runs, so a filter
-- cannot produce a diagram the ordinary way. It can, however, ask for the same
-- runtime Quarto would have loaded and emit the markup that runtime looks for
-- — which means the graph is drawn by Quarto's own bundled Mermaid rather than
-- by anything fromwhere ships or fetches.
local function mermaid(node)
  local share = os.getenv("QUARTO_SHARE_PATH")
  local source = tostring(node.value or "")
  if share and quarto.doc.is_format("html:js") then
    if not diagrams_ready then
      local function asset(name)
        return pandoc.path.join({ share, "formats", "html", "mermaid", name })
      end
      -- The same files, in the same order, with the same dependency name
      -- Quarto uses for its own diagrams — so a document that also has
      -- ordinary Mermaid cells loads one copy rather than two. Plain scripts,
      -- not modules: the bundle sets `globalThis.mermaid` with a top-level
      -- `var`, which module scoping would keep to itself.
      quarto.doc.add_html_dependency({
        name = "quarto-diagram",
        scripts = { asset("mermaid.min.js"), asset("mermaid-init.js") },
        stylesheets = { asset("mermaid.css") },
      })
      diagrams_ready = true
    end
    return pandoc.Blocks({
      pandoc.RawBlock(
        "html",
        '<pre class="mermaid mermaid-js">' .. escape(source) .. "</pre>"
      ),
    })
  end
  -- No Mermaid runtime here — PDF, docx, plain HTML. The source is shown
  -- rather than dropped: a reader can paste it somewhere that draws it, and a
  -- panel that silently loses a section is worse than one that is ugly.
  return pandoc.Blocks({
    pandoc.CodeBlock(source, pandoc.Attr("", { "default" }, {})),
  })
end

local function callout(node)
  local title, rest = "", {}
  for _, child in ipairs(node.children or {}) do
    if child.type == "admonitionTitle" then
      title = pandoc.utils.stringify(inlines(child.children))
    else
      table.insert(rest, child)
    end
  end
  local attributes = { { "title", title } }
  if node.class == "dropdown" then
    attributes[#attributes + 1] = { "collapse", "true" }
  end
  return pandoc.Div(
    blocks(rest),
    pandoc.Attr("", { CALLOUT[node.kind] or "callout-note" }, attributes)
  )
end

local function block(node)
  local kind = node.type
  if kind == "paragraph" then
    return pandoc.Blocks({ pandoc.Para(inlines(node.children)) })
  elseif kind == "table" then
    return pandoc.Blocks({ data_table(node) })
  elseif kind == "mermaid" then
    return mermaid(node)
  elseif kind == "admonition" then
    return pandoc.Blocks({ callout(node) })
  elseif kind == "image" then
    return pandoc.Blocks({ pandoc.Plain({ image(node) }) })
  elseif kind == "embed" then
    return embed(node)
  end
  warn("unknown block node `" .. tostring(kind) .. "`")
  return pandoc.Blocks({})
end

blocks = function(nodes)
  local out = pandoc.Blocks({})
  for _, node in ipairs(nodes or {}) do
    out:extend(block(node))
  end
  return out
end

-- --------------------------------------------------------------------------
-- The directives
-- --------------------------------------------------------------------------

local function options_of(element)
  local out = {}
  for key, value in pairs(element.attributes) do
    out[key] = value
  end
  return out
end

--- Said in the document rather than only in the log.
-- A missing install would otherwise show up as figures quietly losing their
-- provenance, which is the one failure mode this tool must not have.
local function unavailable(element)
  local body = pandoc.Blocks({
    pandoc.Para(
      words(
        "fromwhere could not be run, so there is no provenance to show here. "
          .. "Install it in this document's environment, or set "
          .. "FROMWHERE_QUARTO to the path of `fromwhere-quarto`."
      )
    ),
  })
  body:extend(element.content)
  return pandoc.Div(
    body,
    pandoc.Attr("", { "callout-important" }, { { "title", "fromwhere" } })
  )
end

--- Said in the document, because the alternative here is silence.
-- A retired class is just an unknown class to Pandoc: the div renders as a
-- plain box and the panel vanishes without anything being wrong enough to
-- report. A document that quietly stopped showing its provenance is exactly
-- the failure this tool exists to prevent.
local function retired(element)
  local body = pandoc.Blocks({
    pandoc.Para(
      words(
        "`.fromwhere-provenance` was replaced by `.fromwhere` with no `src`. "
          .. "Move any options across unchanged."
      )
    ),
  })
  body:extend(element.content)
  return pandoc.Div(
    body,
    pandoc.Attr("", { "callout-important" }, { { "title", "fromwhere" } })
  )
end

local function fromwhere_div(element)
  local result = call("fromwhere", {
    options = options_of(element),
    base = document_directory(),
  })
  if result == nil then
    return unavailable(element)
  end

  local out = pandoc.Blocks({})
  local caption = element.content
  local body = result.body or {}
  if (result.kind == "figure" or result.kind == "interactive") and body[1] then
    -- A picture and an interactive chart differ only in what goes inside the
    -- figure; both are figures to a reader and both should be numbered.
    local content = result.kind == "figure"
        and pandoc.Blocks({ pandoc.Plain({ image(body[1]) }) })
      or blocks(body)
    if element.identifier ~= "" or #caption > 0 then
      -- A Quarto figure: numbered, cross-referenceable, captioned. The caption
      -- comes from the div rather than from fromwhere, so citations and
      -- cross-references inside it keep working.
      out:insert(
        pandoc.Figure(
          content,
          { long = caption },
          pandoc.Attr(element.identifier, {}, {})
        )
      )
    else
      out:extend(content)
    end
  elseif result.kind == "table" then
    local rendered = blocks(body)
    for index, item in ipairs(rendered) do
      -- The label and caption belong on the table itself; anything after it is
      -- the note saying the rows were truncated.
      if index == 1 and item.t == "Table" then
        item.attr = pandoc.Attr(element.identifier, {}, {})
        if #caption > 0 then
          item.caption = { long = caption }
        end
      end
      out:insert(item)
    end
  else
    out:extend(blocks(body))
    out:extend(caption)
  end

  out:extend(blocks(result.panel))
  return out
end

local function handle(element)
  -- Checked before `fromwhere`, because `fromwhere-provenance` does not contain it
  -- as a class but a document being migrated may well carry both.
  if element.classes:includes("fromwhere-provenance") then
    return retired(element)
  end
  if element.classes:includes("fromwhere") then
    return fromwhere_div(element)
  end
  return nil
end

function Pandoc(doc)
  -- Read before walking, so `fromwhere: command:` is known by the time the first
  -- div needs it. A `Meta` handler would be traversed after the blocks.
  local meta = doc.meta.fromwhere
  if meta and meta.command then
    configured = pandoc.utils.stringify(meta.command)
  end
  return doc:walk({ Div = handle })
end
