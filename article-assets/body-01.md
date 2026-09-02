I scrape product cards from online stores for a content bank with [an Actor of my own](https://apify.com/isolovyev/ru-product-cards). The Actor finishes, the dataset fills up, and then a person exports it, checks it, and pastes it into the tool where the work actually happens. That last step has no technology in it. It is somebody's Tuesday.

When Apify shipped [MCP connectors](https://docs.apify.com/platform/integrations/mcp-connectors), I wanted to delete that step. A connector lets an Actor call a third-party service over the [Model Context Protocol](https://modelcontextprotocol.io/) using credentials the user authorized once, on the platform. The Actor never holds the token. It talks to an Apify proxy with its own run token, and the platform injects the real credentials server-side.

I built a small Actor to try it: take product cards, write them into whatever service the user connected. The full code is at [github.com/isolovyev77/apify-card-sink](https://github.com/isolovyev77/apify-card-sink). It works now. Getting there took six failed runs, and every one of them failed differently.

Versions matter in this article more than usual, because one of the failures is purely a version mismatch. Mine: `apify==3.4.1`, `mcp>=1.2.0`, `httpx==0.28.1`, on the `apify/actor-python:3.12` base image. The platform reported the run as SDK 3.4.1, client 2.5.1, Crawlee 1.9.0.

## What the Actor does

One job: take cards by value, connect to the service the user picked, find a tool that can write, write the rows, and report what happened.

The design decision worth explaining is that the Actor does not know its destination. It does not have a Notion branch and a Supabase branch. It asks the connected service which tools it exposes, picks one that can write, reads the argument schema that service published, and shapes the call to match. Connect it to a Notion workspace and it creates pages. Connect it to a database and it inserts rows.

## Declaring the connector

A connector is an input field with `resourceType: "mcpConnector"`, which the [Actor-side guide](https://docs.apify.com/platform/integrations/mcp-connectors/use-in-actors) documents in full. The `mcpServers` list does two jobs at once: it filters which of the user's connectors show up in the picker, and it caps what the Actor may call at runtime. The proxy holds you to the declaration, so a tool you did not declare is not just discouraged, it is unreachable.

```json
{
    "outputConnector": {
        "title": "Destination",
        "type": "string",
        "description": "Where to write the cards. Pick a connector you have authorized: a Notion workspace, a Supabase project, a Slack channel. Your credentials stay on the Apify side and never reach this Actor.",
        "resourceType": "mcpConnector",
        "editor": "resourcePicker",
        "mcpServers": [
            { "url": "*", "tools": { "required": ["insert*"] } },
            { "url": "*", "tools": { "required": ["create_*"] } },
            { "url": "*", "tools": { "required": ["post_*"] } }
        ]
    }
}
```

In Apify Console the field renders as a picker, and the connector the user chose is what the
run receives:

![The Actor input form in Apify Console, showing a Destination field with the Notion connector selected](https://raw.githubusercontent.com/isolovyev77/apify-card-sink/main/article-assets/01-apify-input-schema-mcp-connector-picker-notion.jpg)
*The `mcpConnector` field as the user sees it. No token, no endpoint, just a choice.*

That declaration is where my first real mistake lives, and I will come back to it.

## Six runs, six different failures

![Run history of the Actor in Apify Console: nine runs, four succeeded with one result each, two failed with an exception](https://raw.githubusercontent.com/isolovyev77/apify-card-sink/main/article-assets/02-apify-console-actor-run-history-mcp-connector.jpg)
*Nine runs in one night. The two red ones are where the MCP client crashed before reaching Notion.*

**Run one lasted three seconds and produced nothing.** Exit code 0, empty dataset, no error anywhere. I had written `async def main()` and never called it. The module imported cleanly, defined a function, and exited. On a platform that reports success by exit code, forgetting `asyncio.run(main())` looks exactly like an Actor with nothing to do.

**Run two also produced an empty dataset**, this time correctly: no connector was selected, so the Actor logged a warning and returned. The behaviour was right and the reporting was wrong. I had just written an article arguing that a caller sees the dataset and never the log, and here I was, putting the one fact the caller needed into the log. Now every refusal is a row:

```python
async def refuse(status, detail):
    Actor.log.warning(detail)
    await Actor.push_data({"delivered": 0, "status": status, "detail": detail})

if not connector_id:
    return await refuse("no_connector", "no connector selected: nowhere to write")
```

**Run three crashed inside the MCP client**, before a single byte reached Notion:

```text
ValueError: not enough values to unpack (expected 3, got 2)
  File "/usr/src/app/src/main.py", line 102, in main
    http_client=http_client) as (read, write, _):
```

The Python example in the Apify documentation unpacks three streams from `streamable_http_client`. The version of the MCP SDK that installed in my image yields two. Both are correct for their own version; a fixed unpack is what breaks. Index the result instead:

```python
async with streamable_http_client(f"{proxy_url}/{connector_id}",
                                  http_client=http_client) as streams:
    read, write = streams[0], streams[1]
    async with ClientSession(read, write) as session:
        await session.initialize()
```

**Run four connected, and the tool matcher came up empty.** My write hints were written for the names I imagined: `create_page`, `insert`, `append`. Notion exposes `notion-create-pages`. Different word, different separator. The naive repair is to match on `create`, which then also matches `notion-create-attachment` and `notion-create-file-upload`, and product cards do not belong in either. You can see the full set the connector exposes in Console:

![The MCP connectors section in Apify Console settings, listing 27 Notion tools including notion-create-pages and notion-update-page](https://raw.githubusercontent.com/isolovyev77/apify-card-sink/main/article-assets/03-apify-console-mcp-connector-notion-tools-list.jpg)
*Twenty-seven tools, every name hyphenated. This screen is also where you limit what an Actor may call.*

The list is explicit now:

```python
WRITE_HINTS = ("create-pages", "create_pages", "create-page", "create_page",
               "insert", "add_row", "append", "execute_sql",
               "send_message", "post_message")
```

That is also why the `mcpServers` declaration above is wrong in a way you will not notice until a connector fails to appear in the picker. Patterns like `create_*` never match `notion-create-pages`. While I was learning what services actually name their tools, I widened the declaration to `[{"url": "*"}]` and let the code do the filtering. Narrow it back once you know the names you need.

**Run five wrote nothing because I could not see the argument shape.** My dry run reported the tool it would use and a `null` where the schema should be. `getattr(tool, "inputSchema", None)` returned an object that did not survive the JSON dump. The SDK hands back pydantic models, and the field is `input_schema` in a dump, not `inputSchema`:

```python
def describe(tool):
    """Argument shape of a tool, in a form that survives a JSON dump."""
    for attr in ("model_dump", "dict"):
        fn = getattr(tool, attr, None)
        if callable(fn):
            try:
                return fn(mode="json") if attr == "model_dump" else fn()
            except TypeError:
                return fn()
    return {"name": getattr(tool, "name", None), "note": "shape unavailable"}
```

With that fixed, the dry run returned all 27 tools the Notion connector exposes and the full schema of the one it picked. That output is the single most useful thing this Actor produces, which is why `dryRun` is a first-class input rather than a debug flag.

## There is no universal write call

Reading that schema killed my original design. I had assumed a write is a write: hand the tool a table name and a list of rows. Notion wants pages, each with a title property and a Markdown body. A database wants rows. A chat wants a channel and a text blob.

So the Actor stopped guessing from the tool name and started reading the published schema:

```python
def build_arguments(tool, payload):
    """Shape the call for the tool we picked."""
    props = schema_of(tool)
    if "pages" in props:
        pages = [{"properties": {"title": c.get("title") or "Untitled product card"},
                  "content": as_markdown(c)} for c in payload["rows"]]
        return {"pages": pages}, "notion-style pages"
    for key in ("rows", "records", "values"):
        if key in props:
            return {key: payload["rows"], "table": payload["table"]}, "table rows"
    if "text" in props or "message" in props:
        body = "\n\n".join("%s - %s" % (c.get("title"), c.get("url"))
                           for c in payload["rows"])
        return {"text": body, "channel": payload["table"]}, "chat message"
    return payload, "unrecognised argument shape, sending our own"
```

The last branch matters as much as the first three. When the shape is unfamiliar, the Actor says so in the dataset instead of sending a hopeful payload into somebody's workspace.

One detail from Notion's schema saved me a whole feature: the parent is optional. Without it, created pages land as private workspace-level pages. I had been about to build a parent-search step, and the service documentation had already answered it.

Run six wrote for real. One card in, one page out:

```json
{
  "delivered": 1,
  "status": "ok",
  "tool": "notion-create-pages",
  "argumentShape": "notion-style pages",
  "response": "{\"pages\":[{\"id\":\"3b3b52ac-9795-818b-b31c-fcc9852969ce\",\"properties\":{\"title\":\"Lenovo IdeaPad Slim 3 15ABR8\"}}]}"
}
```

![Apify Console log of the successful run, showing 27 tools discovered, the notion-create-pages call and the response with the created page id](https://raw.githubusercontent.com/isolovyev77/apify-card-sink/main/article-assets/04-apify-run-log-notion-create-pages-mcp-connector.jpg)
*The whole exchange in five log lines: connect, list tools, pick one, call it, get a page id back.*

![Apify dataset view of the run, one row with delivered 1, status ok, tool notion-create-pages](https://raw.githubusercontent.com/isolovyev77/apify-card-sink/main/article-assets/05-apify-dataset-mcp-connector-delivered-notion.jpg)
*The dataset carries what happened, including which argument shape was used.*

The page appeared in the workspace with the product title as its heading and the card body in Markdown underneath.

![The resulting Notion page titled Lenovo IdeaPad Slim 3 15ABR8, with platform, price, specification count, image count and a link to the product](https://raw.githubusercontent.com/isolovyev77/apify-card-sink/main/article-assets/06-notion-page-created-by-apify-actor-mcp-connector.jpg)
*The end of the last mile: a card that arrived without anyone exporting anything.* Total time from the Actor starting to the page existing: under ten seconds, and my Actor never saw a Notion token.

## The connector I could not use

I started with Supabase, because I already run one. Apify accepted the server URL, then told me the server does not support dynamic client registration and recommended registering my own OAuth application. Notion, by contrast, has managed OAuth: pick it from the dropdown, authorize, done.

This is worth checking before you design around a service. Apify provides managed OAuth for Notion and Supabase; for GitHub, Slack, Google and others you bring your own OAuth client. Supabase also accepts a personal access token through the API key method, which is the shortcut if you need that one specifically.

## What the security model actually buys you

Three layers decide what a connector-enabled Actor can do, and they compose: the scopes granted at authorization, the tool allowlist the user sets on the connector in Console, and the Actor's own `mcpServers` declaration. The proxy filters `tools/list` down to what the Actor declared and rejects calls outside it.

For a published Actor this is the difference between "give me your API key" and "pick a connector". My Actor never sees a Notion token. A user who wants to be stricter can allow only page creation on their connector, and the Actor cannot get around it. Access dies with the run.

## Deploying it, and a wall I hit on the way

I deploy Actors by pushing source files through the [Apify API](https://docs.apify.com/api/v2). That stopped working here: bodies over roughly 20 KB left my machine intact and never came back with a response. Rather than fight it, I switched the Actor to build from Git.

```text
sourceType: GIT_REPO
gitRepoUrl: https://github.com/isolovyev77/apify-card-sink#main
```

The tiny request body sidestepped the problem entirely, and the article got a public repository as a side effect. If you are debugging an Actor through repeated deploys, this is the better default anyway: the build log tells you which commit it built.

## What I would tell someone starting this today

1. Ship `dryRun` before you ship the write path. List the tools, dump the schema, write nothing. Everything else in this article was discovered by that one code path.
2. Declare `{"url": "*"}` while you learn the names, then narrow. A pattern that does not match leaves the user staring at an empty picker with nothing to click.
3. Read the tool's published schema instead of matching on its name. Names vary by service and separator; schemas do not lie.
4. Put every refusal in the dataset. The caller sees rows, not logs, and this is doubly true when the caller is an agent.
5. Check the authentication method for your service before you design around it. Managed OAuth and "register your own OAuth app" are half an hour apart.

The last mile is closed now. Cards land where the work happens, and nobody exports a spreadsheet on Tuesday.
