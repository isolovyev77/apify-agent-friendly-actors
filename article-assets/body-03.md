A client asked a question that sounds simple: which sellers on the marketplaces are risky to buy from? Answering it takes two steps. Find the sellers behind the product listings, then check each seller's legal entity against public risk registries.

I had an Actor for each half already. [One](https://apify.com/isolovyev/marketplace-seller-leads) walks marketplace search results and returns sellers with their tax ID, legal name and store URL. [The other](https://apify.com/isolovyev/ru-counterparty-check) takes tax IDs and returns a risk score built from bankruptcy filings, tax arrears, the procurement blacklist and the financial monitoring list. Both had been in the Store for months, both had paying users.

![Run history of the marketplace seller lead-generation Actor in Apify Console, with runs returning 15, 98, 68, 100, 50 and 40 results](https://raw.githubusercontent.com/isolovyev77/apify-agent-friendly-actors/main/article-assets/chaining-01-apify-marketplace-seller-leads-actor-runs.jpg)
*The first Actor, doing its half of the job. Each of these runs produces the input for the second one.*

Handing the chain to an agent broke it three times, and none of the breaks looked like a failure. The runs stayed green. The dataset filled up. The answer was wrong in a way that only showed up when I checked a company by hand.

The agent reached both Actors through the [Apify MCP server](https://docs.apify.com/platform/integrations/mcp), which exposes them as tools over the [Model Context Protocol](https://modelcontextprotocol.io/). Nothing below depends on which client you use: the failures come from how the two Actors hand data to each other, and a human doing the same handoff by hand would have hit two of the three.

## Why this is two Actors and not one

The scraper needs a browser, residential proxies and a container image heavy enough to carry them. The risk check needs plain HTTP requests to four public sources and nothing else. Merged into one Actor, every risk lookup would drag a browser image behind it.

They also run on different clocks. Seller lists get refreshed when a client enters a new category. Risk data gets rechecked weekly for the same sellers, because bankruptcy filings appear on their own schedule. Splitting them means the expensive half runs once and the cheap half runs as often as the client wants.

That split is the right call for the platform and the wrong shape for a naive agent, which will treat the two as one tool and wire them together the first way that looks plausible.

## Break one: the handoff by reference

The obvious wiring is to pass the first run's dataset id to the second Actor. My builder Actors accept `sourceDatasetId` for exactly that. It works on a laptop, where your personal token reads everything you own.

On the platform, an Actor running with standard permissions cannot read the default dataset of a different run. The API answers `Insufficient permissions`. I confirmed this on a cloud run on 25 July after losing most of a day to it locally, where [the Python SDK](https://github.com/apify/apify-sdk-python) happily used my personal token and hid the whole problem.

The fix is to pass values, and the part specific to chaining is this: your input schema decides which wiring the agent tries first. If `sourceDatasetId` sits at the top of the schema with a confident description, that is the path the agent picks. I moved `items` to the top, described it as the primary input, and demoted the dataset id to a fallback with an explicit note about permissions.

```json
{
  "items": {
    "title": "Companies to check",
    "type": "array",
    "description": "Records with an inn field, passed by value. Use this when chaining from another Actor: a run cannot read another run's dataset with standard permissions.",
    "editor": "json"
  },
  "sourceDatasetId": {
    "title": "Source dataset (same-account only)",
    "type": "string",
    "description": "Read companies from a dataset your token owns. Fails with Insufficient permissions across runs."
  }
}
```

Schema order is documentation for a reader who stops reading after the first field that works.

## Break two: the first Actor's output is the second one's input, and mine did not fit

![Apify Store page of the counterparty due diligence Actor, which takes tax IDs and returns a risk score from public registries](https://raw.githubusercontent.com/isolovyev77/apify-agent-friendly-actors/main/article-assets/chaining-02-apify-store-counterparty-due-diligence-actor.jpg)
*The second Actor has no idea where its tax IDs come from. That indifference is what makes it reusable, and what makes the handoff fragile.*

The seller scraper returns a tax ID for most sellers, not all. On Wildberries the ID lives in a separate dossier request that I make only when `fetchDossier` is on, and some storefronts have no legal entity attached at all. My run log said it plainly:

```python
with_inn = sum(1 for s in sellers if s.get("inn"))
Actor.log.info(f"sellers: {len(sellers)} (with tax ID: {with_inn}, by platform: {scraped_counts})")
```

The agent never sees the log. It saw a list of sellers, mapped it to the risk checker's input, and passed the whole thing. Records without a tax ID went in as empty strings. The risk Actor did what it should: no company found, no risk data, one record out per record in.

The result read as "these eleven sellers came back clean." They had never been checked.

Two changes fixed it. First, the seller Actor now marks each record with what a downstream step can do with it:

```python
seller["checkable"] = bool(seller.get("inn"))
seller["checkableReason"] = None if seller.get("inn") else "no legal entity published by the marketplace"
```

Second, the risk Actor refuses to invent an answer for input it cannot use:

```python
inn = (raw.get("inn") or "").strip()
if not inn:
    await Actor.push_data({
        "input": raw.get("name") or raw.get("sellerId"),
        "lookupStatus": "skipped_no_inn",
        "riskScore": None,          # not zero: absence of a check, not absence of risk
    })
    continue
```

`riskScore: None` is the important part. A zero score means four sources answered and found nothing. `None` with a status means nobody looked. An agent summarising fifty sellers will average those numbers, and a zero in that average is a claim I never made.

## Break three: a schema rejection ate the results of a paid run

This one is my favourite, because everything about it was working as designed.

My risk Actor labels each risk factor with its source. The dataset schema declared the allowed values as an enum, which is good practice: it keeps the output honest and gives the Store a clean field list. The enum listed `fedresurs` and `nalog`, the two sources I had at the time.

Then I added the procurement blacklist as a third source, labelled `rnp`. Company records that carried a blacklist factor now failed validation on write. `push_data` raised `Schema validation failed`, my handler logged a warning, and the run continued and finished green.

Read that again in the context of a chain. The scraper ran and charged. The risk Actor ran and charged. The most interesting companies in the batch, the ones actually on a blacklist, were the exact records that never reached the dataset. The agent received a shorter list with no indication that anything was missing, and every company on it looked fine.

The fix took one line in the schema:

```json
{
    "riskFactors": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["fedresurs", "nalog", "rnp", "rosfinmonitoring"]
                }
            }
        }
    }
}
```

I shipped it as build 0.1.16, then verified against three tax IDs on the platform rather than locally: run `b1J1ULvJJuVL7beZd` came back with all three non-empty, including `7536176707` with 23 blacklist records and a risk score of 90. When I later added the financial monitoring list I extended the enum in the same commit as the parser, before any run could produce the new value.

Two habits came out of this:

- Treat a write failure as a run failure. A warning in the log is invisible to the caller, and in a chain the caller is a machine that already paid for the data.
- Extend output enums in the same commit that produces the new value. There is no version of this where the parser lands first and the schema catches up later.

```python
try:
    await Actor.push_data(out_items)
except Exception as exc:
    # the full text matters: the platform names the field that failed validation,
    # and without it a chain silently loses its most interesting records
    Actor.log.warning(f"push_data failed ({type(exc).__name__}): {str(exc)[:220]}")
    raise
```

## What the agent can actually hold

A category sweep returns a few hundred sellers. The full risk record for one company runs to dozens of fields: bankruptcy cases, arrears by year, directors, encumbrances, appeals. Multiply that out and no agent can keep the result in context, so it starts summarising, and the summary is where accuracy goes to die.

I added a compact mode that returns one row per company with the decision fields only:

| Field | Meaning |
|---|---|
| `inn` | tax ID that was checked |
| `riskScore` | 0-100, or `null` when no check happened |
| `riskLevel` | low, medium, high |
| `topFactors` | up to three factor codes that drove the score |
| `lookupStatus` | ok, not_found, source_unreachable, skipped_no_inn |

Full dossiers stay in the dataset for a human to open. The agent gets five fields per company, which fits, and it can request the full record for the handful that matter. My prefill company, tax ID `7707083893`, comes back as `riskScore: 35, riskLevel: medium`, which is one line instead of six screens.

## Paying twice for the same answer

Chained runs invite repetition. An agent that gets a partial result retries the step, and a retry of step two is a second charge for companies that were already checked in the first attempt. My billing is one event per company, so a fifty-seller batch retried once costs a hundred events for fifty answers.

The cheap defence is to make repetition visible before it happens. The risk Actor now drops duplicate tax IDs inside a single input, reports how many it dropped, and refuses to charge for a company it already answered in the same run:

```python
seen, deduped = set(), []
for raw in items:
    key = (raw.get("inn") or "").strip()
    if key and key in seen:
        continue
    seen.add(key)
    deduped.append(raw)

if len(deduped) < len(items):
    Actor.log.info(f"duplicate inputs dropped: {len(items) - len(deduped)}")
```

Across runs the responsibility shifts to the caller, and the caller is an agent with no memory of yesterday. What helps there is making the record self-dating: every row carries `checkedAt`, so an agent that keeps its own store can decide whether a seven-day-old risk score needs refreshing before it spends money on one.

## What I would do differently from the start

I built both Actors for a person clicking Start in the console, then adapted them for a chain. Building for the chain first would have changed four decisions:

1. Every record states whether the next step can use it, and why not when it cannot.
2. Absence of a check and absence of a finding are different values, never both `0` or both `[]`.
3. A failed write ends the run instead of shortening the dataset.
4. There is a compact output shape, because the second consumer of your data has a context limit and the first one had a monitor.

The chain works now. An agent runs the seller sweep, passes the checkable records by value, gets five fields back per company, and asks for full dossiers only where the score crosses a threshold. What made it work was not new scraping code. It was making both Actors honest about what they did not do.
