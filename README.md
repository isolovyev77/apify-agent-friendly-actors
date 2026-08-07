# apify-agent-friendly-actors

Runnable patterns pulled out of fourteen Apify Actors after an AI agent started calling them
and the results stopped making sense. Companion code for two articles in the Apify writing
program:

- *My Actors worked fine until an AI agent called them* - input schemas, tri-state outputs,
  billing order, and what the platform's own automated test taught me.
- *Two Actors, one agent, and the three ways my chain broke* - what happens when an agent
  wires two Actors together.

Each file in `examples/` runs on its own and prints what it demonstrates. They are extracted
from production Actors, with the store-specific parts replaced by fixtures so you can run
them without an Apify account.

## The patterns

| File | Problem it solves |
|---|---|
| `examples/tri_state_lookup.py` | An empty result means "not found" and "could not check" at the same time. An agent cannot tell them apart and treats both as fact. |
| `examples/chaining_by_value.py` | A dataset id from one run is unreadable by another run's Actor. Chained tools must hand over values. |
| `examples/charge_before_write.py` | Dataset rows are append-only. Anything computed after `push_data` never reaches the caller. |
| `examples/dedupe_inputs.py` | An agent that retries a step pays twice for answers it already has. |
| `examples/input_schema_resolved_codes.json` | An input field that only a person with the target site open could fill. |

## Running them

```bash
python3 examples/tri_state_lookup.py
python3 examples/chaining_by_value.py
python3 examples/charge_before_write.py
python3 examples/dedupe_inputs.py
```

No dependencies: the examples use the standard library and stub out the Apify SDK calls, so
the pattern stays visible without an account or a network. In the real Actors these run on
`apify==3.4.1`.

## Related

- [Actor that delivers cards through an MCP connector](https://github.com/isolovyev77/apify-card-sink)
- [My Actors on Apify Store](https://apify.com/isolovyev)

## License

MIT
