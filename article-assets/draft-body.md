On 27 July I published a scraper that pulls product cards and specifications from online stores. It was my fourteenth Actor. The others cover company due diligence by tax ID, government tender feeds, cadastral records, marketplace seller leads. All of them had months of green runs behind them.

Then I connected the [Apify MCP server](https://docs.apify.com/platform/integrations/mcp) to Claude. It exposes Actors as tools an AI client can call over the [Model Context Protocol](https://modelcontextprotocol.io/). I gave it a task in plain language: find this product across those stores and return the specs.

The agent picked my Actor, filled the input, started the run. Status: succeeded. Zero results, no error.

![Run history in Apify Console with the Origin column reading MCP for the most recent run](https://raw.githubusercontent.com/isolovyev77/apify-agent-friendly-actors/main/article-assets/01-apify-run-history-origin-mcp-agent-call.jpg)
*The Origin column says MCP: that run was started by an agent, not by me clicking Start.*

The scraper worked. The agent had read my input schema the way any careful reader would. The trouble is that I wrote that schema for a reader who already had the target website open in another tab, and that reader was me, six months earlier.

This is what I changed across fourteen Actors to make them callable by something that cannot ask me a question. Two of those changes were about input fields. The ones that cost me money were about billing, and about the gap between "nothing exists" and "I could not look."

The code below is Python, using the [Apify SDK for Python](https://github.com/apify/apify-sdk-python) version 3.4.1, and every snippet is lifted from an Actor that runs in production today.

## Why an agent breaks things a human never would

A human who gets an empty dataset has options. They open the store page, reread the README, try another value, send me an email. The whole recovery loop takes five minutes and never reaches me.

An agent has one shot inside a longer plan. It reads your schema, picks values, fires the run, reads the output, and branches. If your Actor answers six different situations with the same empty array, the agent treats all six as one fact and keeps executing with confidence. The wrong branch is now running, and nothing looks broken anywhere.

So the question stopped being "is my schema documented" and became: can a caller who has never seen the target site tell my outcomes apart?

## My input fields described the website, not the task

The clearest case came from my software registry Actor. It queries a national software registry, which groups products into classes: `06.09` is a class, `06` is the group above it. Those codes are printed on the site and appear in every document a user would quote.

The registry's listing endpoint ignores them. It filters by an internal option id, an opaque integer that lives only inside their markup. You learn it by loading the listing page and mapping labels to ids. My first version exposed that integer, because that is what the request needed.

No caller can produce that value. An agent asked about class `06.09` passes `06.09`, gets a clean run and an empty dataset. An agent that guesses an integer gets a full dataset for the wrong class, which costs more, because nobody checks a result that looks right.

I moved the translation inside the Actor:

```json
{
  "softwareClasses": {
    "title": "Software classes",
    "type": "array",
    "editor": "stringList",
    "description": "Class codes as printed on the registry site: 06.09 for one class, 06 for the whole group. The Actor resolves them to the registry's internal filter ids.",
    "prefill": ["06.09"]
  }
}
```

![Input schema of the Actor as rendered on its Apify Store page, showing field descriptions for product URLs and product names](https://raw.githubusercontent.com/isolovyev77/apify-agent-friendly-actors/main/article-assets/02-apify-actor-input-schema-field-descriptions.jpg)
*The input as a caller sees it. Every sentence here is the only instruction an agent will ever get.*

The rule I now apply to every input field: if a value can only be obtained by inspecting the target site, keep it out of the schema. Resolve it in code. When you cannot, make it an `enum` with readable titles, so the caller picks from a closed list instead of inventing. The [input schema specification](https://docs.apify.com/platform/actors/development/actor-definition/input-schema/specification/v1) supports both, and the choice between them is the whole difference between a caller that guesses and one that picks.

That rule caught a second field the same week. My product Actor takes a `platforms` list, which used to be free-form strings matching my internal module names. Agents passed `Ozon`, `ozon.ru`, `OZON`. My code wanted `ozon`. It is an enum now, and unknown values get reported in the output instead of dropped.

## Empty and failed looked identical, and that cost a client money

The registry sits behind a certificate chain from a national CA that no default trust store carries. Requests die with `CERTIFICATE_VERIFY_FAILED`. When I first hit that, I caught the exception, logged a warning, and returned an empty result for the company.

Empty result. The same thing the Actor returns when a company is genuinely absent from the registry.

For one company at a time, a human reads the warning line and moves on. My client ran two hundred tax IDs through an agent. About half the "not in the registry" answers were "the source did not answer," and both arrived as `[]`. Those answers flowed into a decision about which suppliers to work with.

Every record now carries the reason it looks the way it does:

```python
record = {
    "inn": inn,
    "foundInRegistry": None,          # True, False, or None when we could not check
    "lookupStatus": "source_unreachable",   # ok | not_found | source_unreachable
    "lookupDetail": "registry TLS chain rejected",
}
```

Three states instead of two. `foundInRegistry: false` claims something about the world. `null` with a status claims something about my attempt. The agent can act on the first and retry the second.

Here is the mapping I now keep for the product scraper, which has four ways to end up with fewer rows than expected:

| What happened | What the record says | What the agent should do |
|---|---|---|
| Store returned a challenge page | `status: blocked`, `specCount: 0` | retry later or route to another store |
| Search matched nothing | `status: not_found` | drop the query, do not retry |
| Page loaded, no specifications | `status: partial`, `title` present | usable for links, not for a card |
| Store search by name unsupported | `status: unsupported_mode` | switch to direct URLs |

Before this table existed, all four rows were an empty array and a warning in the log the caller never sees.

## Dataset ids do not cross run boundaries

My product pipeline is two Actors by design. [The scraper](https://apify.com/isolovyev/ru-product-cards) needs a browser, proxies and a heavy image. The card builder needs one HTTP request for a category reference and no browser at all. Splitting them means the expensive half runs once and the cheap half re-runs every time a client changes the card format.

I wired them the obvious way: the builder takes a `sourceDatasetId` and reads the scraper's output. On my machine it worked on the first try.

On the platform it fails. An Actor with standard permissions cannot read the default dataset of a different run, and the API answers `Insufficient permissions`. I confirmed it on a cloud run on 25 July, after losing a day locally, because local development uses your personal token and hides the whole problem.

Agents chain tools by default. Claude called the scraper, took the dataset id out of the run result, passed it to the builder, and got a permissions error it had no way to fix. The builder now takes values, and the dataset path is the fallback:

```python
items = inp.get("items") or []
from_dataset = inp.get("sourceDatasetId")

if from_dataset and not items:
    # Works when the caller owns the dataset and supplies a token that can read it.
    # A cross-run read with the Actor's own permissions returns
    # "Insufficient permissions", so agent workflows should pass items.
    page = await Actor.apify_client.dataset(from_dataset).list_items()
    items = page.items

if not items:
    Actor.log.warning("No input records: pass items, or a dataset this run can read.")
    return
```

If you build a family of Actors meant to compose, make values the interface and references the optimisation. The agent holds that data in its context anyway.

## Billing is part of the interface

My scraper charges per event: one price for a card assembled from free methods, another when a paid fallback channel was required, plus a surcharge for building the publish-ready structure. The run decides which events fire based on what the store made it do.

Two things went wrong, and both belong to callers who never read a pricing page.

![Pricing tab of the Actor on Apify Store, listing three pay-per-event prices: basic card, premium card and ready-to-publish card](https://raw.githubusercontent.com/isolovyev77/apify-agent-friendly-actors/main/article-assets/03-apify-actor-pay-per-event-pricing-tab.jpg)
*Three events, three prices. Which ones fire is decided by the run, so an agent cannot predict the bill from the schema alone.*

**The surcharge shipped as a default.** The flag that builds the ready-to-publish card started as `true`, because that output demos well. An agent calling the Actor for specifications paid for a formatting step it never used and never mentioned. I flipped `buildCards` to `false` and put the price consequence in the field description. Anything that costs extra should be opt-in, priced in the text the caller actually reads.

The check that catches this takes a minute. Run the Actor in the cloud, pull `chargedEventCounts` off the run object, compare it against the events you expected. Not the local run. The billed one.

**I charged in the wrong order,** and I had a reason that sounded good. I wanted every dataset row to state what it had been billed as, so a client could reconcile an invoice line by line, and I did not want to charge for a card that failed to save. So the run pushed the row, then charged, then set `billedAs`.

Datasets are append-only. After `push_data` returns, that row is frozen. Assigning `billedAs` afterwards changed an object in my process and nothing else. Clients received `billedAs: null` while the invoice showed charges, which killed the one feature the field existed for.

```python
# Charge BEFORE the write. The reverse order avoided billing for a card that never
# landed, but a dataset row cannot be edited after the fact: billedAs assigned
# afterwards changes only the in-memory object, and the client receives an empty
# field while the invoice shows a charge.
for card_obj, item in to_charge:
    item["billedAs"] = await charge_for_card(card_obj, item["paidMethods"], emit_cards)

await Actor.push_data(out_items)
```

The general form has nothing to do with billing: compute anything the caller needs to see before the immutable write.

While I was in that function I stopped charging for empty results. A page that yielded a title and no specifications is not a product card:

```python
async def charge_for_card(card, paid_stages, build_cards):
    if not card.get("specCount"):
        return None                      # no specs, no card, no charge
    event = EVENT_PREMIUM if paid_stages else EVENT_BASIC
    try:
        await Actor.charge(event)
        if build_cards and (card.get("card") or {}).get("attributeCount"):
            await Actor.charge(EVENT_READY)
    except Exception as exc:
        # billedAs must reflect the FACT of a charge, otherwise reconciliation
        # fails in both directions
        Actor.log.warning(f"charge '{event}' failed: {type(exc).__name__}: {str(exc)[:90]}")
        return None
    return event
```

## The platform's automated test is your first agent

Apify runs your Actor with its prefill input on a schedule and flags it as under maintenance when the run produces nothing. The flag pulls you out of Store search until it clears.

I got flagged twice, and the scraper was healthy both times.

The first prefill hit four marketplaces at once. That is a realistic user request, it takes longer than the test allows, and the test gave up. My Actor sat marked as broken while paying users ran it all day. The prefill is now the cheapest useful request I have: one store, one query, under five minutes. The impressive four-store demo moved to a saved task, where nobody holds a stopwatch.

The second time, [my marketplace monitor](https://apify.com/isolovyev/ru-marketplaces-price-monitor) lost one store's API host and the prefill happened to use that store. The verdict was fair. What I took from it is that your prefill decides which single failure can delist you, so it should sit on your most reliable path rather than your most interesting one. I raised the per-page retry budget, redeployed as build 0.1.46, and the run came back with 200 cards in 96 seconds.

Treat that test as a preview of every agent that will call you: no context, no patience, one attempt, and a verdict based on whether output appeared.

## The checklist I run before an Actor meets an agent

1. Can a caller produce every input value without opening the target site? If not, resolve it in code or use an enum.
2. Does the output separate "no such thing" from "I could not check"? Tri-state fields plus an explicit status.
3. If this Actor is chained with another, does data pass by value? Cross-run dataset reads fail on standard permissions.
4. Is anything that costs money on by default? Turn it off, and state the price where the caller reads.
5. Is every field the caller needs computed before `push_data`?
6. Is the prefill my most reliable path, and does it finish fast?
7. Does `chargedEventCounts` on a real cloud run match what I believe I charge?

None of this made my scrapers better at scraping. It made them usable by a caller who cannot ask me anything, which turned out to be the same as making them usable by anyone other than me.

![Apify Store page of the Russian Marketplace Product Card and Specs Scraper, showing pay-per-event pricing from six dollars per thousand basic cards](https://raw.githubusercontent.com/isolovyev77/apify-agent-friendly-actors/main/article-assets/04-apify-store-russian-marketplace-product-card-scraper.jpg)
*The Actor most of these lessons came from. Pay-per-event pricing is what turned the billing bugs from cosmetic into expensive.*

The two Actors most of this came from are [the product card and specs scraper](https://apify.com/isolovyev/ru-product-cards), which handles the scraping and the pay-per-event side, and [the counterparty due diligence Actor](https://apify.com/isolovyev/ru-counterparty-check), where an empty array first passed for a fact.
